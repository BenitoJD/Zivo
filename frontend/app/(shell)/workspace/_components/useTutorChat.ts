"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  chatSurfaceForMode,
  queryKeys,
  useChatMessagesQuery,
  useSavedNotesActions,
} from "@/lib/api/queries";
import { apiPost, apiPostSSE, ensureGuestSession, humanizeApiFailure } from "@/lib/api/client";
import { isTransientChatAssistantMessage } from "@/lib/constants";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";
import type { McqState } from "@/lib/types";
import type { StudyMode } from "@/app/workspace/_components/studyNav";

export type ChatMessage = { role: string; content: string };

/**
 * Tutor-chat surface, extracted from the workspace page orchestrator.
 *
 * Owns the per-mode conversation: hydration from the server thread, the streaming
 * assistant (send / regenerate / edit-and-resend / stop), Read-mode Study-Buddy
 * helpers (quote a passage, ask directly, save a note), and "clear chat". Returns
 * the same names the page already used so the render is unchanged.
 *
 * The scope sent with each message is mode-aware: Read talks about the document
 * being read (never the Learn queue's page), while Learn/Test pin the current
 * page, question, and the learner's current/confirmed choice.
 */
export function useTutorChat({
  artifactId,
  mode,
  enabled,
  queue,
  currentAssertionId,
  questionPage,
  selected,
  multiSelected,
  isMulti,
  gradeState,
  savedNotesActions,
  onSavedNote,
}: {
  artifactId: string;
  mode: StudyMode;
  enabled: boolean;
  queue: McqState | null;
  /** The question on screen (may differ from queue while feedback is pinned). */
  currentAssertionId?: string | null;
  /** Source page for the active question (newspaper MCQs). */
  questionPage?: number | null;
  selected: string | null;
  multiSelected?: number[];
  isMulti?: boolean;
  gradeState: { correct: boolean; correctIndex: number; correctIndices?: number[] } | null;
  savedNotesActions: ReturnType<typeof useSavedNotesActions>;
  onSavedNote: () => void;
}) {
  const queryClient = useQueryClient();
  const chatSurface = chatSurfaceForMode(mode);
  const chatMessagesQuery = useChatMessagesQuery(artifactId, mode, enabled);

  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatBusy, setChatBusy] = useState(false);
  const chatAbortRef = useRef<AbortController | null>(null);
  const chatHydratedRef = useRef<string | null>(null);

  const chatContextReady = queue?.rag_window_ready !== false;

  // Switching conversation surface (mode) clears the view until the new thread loads.
  // Also abort any in-flight stream: otherwise chatBusy stays true and Read/Learn
  // Send silently no-ops after a mid-stream hop.
  useEffect(() => {
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrate chat thread from server once per surface (chat is appended to locally during streaming)
    setChatBusy(false);
    chatHydratedRef.current = null;
    setChatMessages([]);
  }, [artifactId, chatSurface]);

  useEffect(() => {
    if (!chatMessagesQuery.data || chatBusy) return;
    const key = `${artifactId}:${chatSurface}`;
    if (chatHydratedRef.current === key) return;
    chatHydratedRef.current = key;
    setChatMessages(
      chatMessagesQuery.data
        .filter((m) => (m.content || "").trim())
        .filter((m) => !(m.role === "assistant" && isTransientChatAssistantMessage(m.content)))
        .map((m) => ({ role: m.role, content: m.content })),
    );
  }, [artifactId, chatSurface, chatBusy, chatMessagesQuery.data]);

  // Abort any in-flight stream when the page unmounts.
  useEffect(() => {
    return () => {
      chatAbortRef.current?.abort();
    };
  }, []);

  // Core streamer - assumes chatMessages already ends with the user turn + an empty
  // assistant placeholder to fill. Shared by send, regenerate, and edit-and-resend.
  async function runAssistant(userMsg: string) {
    setChatBusy(true);
    chatAbortRef.current?.abort();
    const abort = new AbortController();
    chatAbortRef.current = abort;
    try {
      await ensureGuestSession();
      // Scope the chat to the CURRENT mode. In Read mode the buddy is about the
      // document the user is reading - never the Learn queue's current page - so
      // it must not pin the learn page/question.
      const scope: Record<string, unknown> = { mode };
      if (mode !== "read") {
        const assertionId = currentAssertionId ?? queue?.current_assertion_id;
        if (assertionId) scope.current_assertion_id = assertionId;
        const page = questionPage ?? queue?.current_page;
        if (page && page > 0) scope.current_page = page;
        if (isMulti && (multiSelected?.length ?? 0) > 0) {
          scope.selected_choice_indices = multiSelected;
          scope.selected_choice_index = multiSelected![0];
        } else if (selected !== null) {
          scope.selected_choice_index = Number(selected);
        }
        if (gradeState !== null) {
          if (isMulti && (multiSelected?.length ?? 0) > 0) {
            scope.confirmed_choice_indices = multiSelected;
            scope.confirmed_choice_index = multiSelected![0];
          } else if (selected !== null) {
            scope.confirmed_choice_index = Number(selected);
          }
          scope.answer_correct = gradeState.correct;
        }
      }
      let assistant = "";
      let gotToken = false;
      await apiPostSSE(
        "/api/chat",
        { document_id: artifactId, message: userMsg, scope },
        {
          onChunk: (chunk) => {
            gotToken = true;
            assistant += chunk;
            setChatMessages((m) => {
              const copy = [...m];
              const i = copy.length - 1;
              const last = copy[i];
              if (last?.role === "assistant") {
                copy[i] = { ...last, content: assistant };
              } else {
                copy.push({ role: "assistant", content: assistant });
              }
              return copy;
            });
          },
        },
        { signal: abort.signal },
      );
      if (!gotToken || !assistant.trim()) {
        throw new Error("empty response");
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setChatMessages((m) => {
          const copy = [...m];
          if (copy[copy.length - 1]?.role === "assistant" && !copy[copy.length - 1]?.content) {
            copy.pop();
          }
          return copy;
        });
        return;
      }
      const raw = e instanceof Error && e.message ? e.message : null;
      const timedOut = raw && /timed out|aborted/i.test(raw);
      const detail = timedOut
        ? `${ZIVO_ASSISTANT_NAME} is still waking up - try again in a moment.`
        : raw && raw !== "empty response"
          ? humanizeApiFailure(0, raw)
          : `${ZIVO_ASSISTANT_NAME} could not reply right now. Try again in a moment.`;
      setChatMessages((m) => {
        const copy = [...m];
        const i = copy.length - 1;
        const last = copy[i];
        if (last?.role === "assistant") {
          copy[i] = { ...last, content: detail };
        } else {
          copy.push({ role: "assistant", content: detail });
        }
        return copy;
      });
    } finally {
      if (chatAbortRef.current === abort) chatAbortRef.current = null;
      setChatBusy(false);
    }
  }

  function sendChat() {
    if (!chatInput.trim() || chatBusy || !chatContextReady) return;
    const userMsg = chatInput.trim();
    setChatInput("");
    setChatMessages((m) => [...m, { role: "user", content: userMsg }, { role: "assistant", content: "" }]);
    void runAssistant(userMsg);
  }

  // Clear the current conversation: empty the panel now, start a fresh thread on the
  // server (per-surface), and drop the cached messages so it stays empty.
  async function clearChat() {
    if (chatBusy) return;
    setChatMessages([]);
    setChatInput("");
    chatHydratedRef.current = `${artifactId}:${chatSurface}`;
    try {
      await apiPost(`/api/chat/threads/${artifactId}/clear?surface=${chatSurface}`, {});
    } catch {
      /* best-effort - the panel is already cleared locally */
    }
    void queryClient.invalidateQueries({ queryKey: queryKeys.chatMessages(artifactId, chatSurface) });
  }

  // Read-mode (Study Buddy) helpers.
  function quoteToComposer(text: string) {
    const t = text.trim().replace(/\s+/g, " ");
    if (!t) return;
    setChatInput(`About this passage:\n"${t}"\n\nMy question: `);
  }
  function askBuddy(message: string) {
    if (chatBusy || !chatContextReady) return;
    const msg = message.trim();
    if (!msg) return;
    setChatMessages((m) => [...m, { role: "user", content: msg }, { role: "assistant", content: "" }]);
    void runAssistant(msg);
  }
  function saveNote(content: string, quote?: string | null) {
    const c = content.trim();
    if (!c) return;
    void savedNotesActions.save(c, quote ?? null);
    onSavedNote();
  }

  // Regenerate the most recent answer: drop the trailing assistant turn and re-ask.
  function regenerateChat() {
    if (chatBusy || !chatContextReady) return;
    const lastUserIdx = chatMessages.map((x) => x.role).lastIndexOf("user");
    if (lastUserIdx === -1) return;
    const lastUser = chatMessages[lastUserIdx].content;
    setChatMessages([...chatMessages.slice(0, lastUserIdx + 1), { role: "assistant", content: "" }]);
    void runAssistant(lastUser);
  }

  // Edit a previous question: lift it into the composer and trim the thread from there.
  function editChatFromUser(index: number) {
    if (chatBusy) return;
    const msg = chatMessages[index];
    if (!msg || msg.role !== "user") return;
    setChatInput(msg.content);
    setChatMessages(chatMessages.slice(0, index));
  }

  function stopChat() {
    chatAbortRef.current?.abort();
  }

  return {
    chatInput,
    setChatInput,
    chatMessages,
    chatBusy,
    chatContextReady,
    sendChat,
    clearChat,
    quoteToComposer,
    askBuddy,
    saveNote,
    regenerateChat,
    editChatFromUser,
    stopChat,
  };
}
