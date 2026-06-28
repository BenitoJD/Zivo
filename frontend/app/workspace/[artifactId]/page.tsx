"use client";

import { use, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Alert,
  Box,
  Button,
  Center,
  Drawer,
  Group,
  Paper,
  Progress,
  SegmentedControl,
  Skeleton,
  Stack,
  Text,
  Title,
  useMantineColorScheme,
} from "@mantine/core";
import { useDisclosure, useMediaQuery } from "@mantine/hooks";
import { IconFileText, IconMessageCircle, IconNotebook } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { apiFetchBytes, apiGet, apiPost, apiPostSSE, apiUrl, ensureGuestSession, humanizeApiFailure, isArtifactId } from "@/lib/api/client";
import {
  chatSurfaceForMode,
  queryKeys,
  useArtifactPagesQuery,
  useArtifactQuery,
  useAssertionQuery,
  useChatMessagesQuery,
  useSavedNotesQuery,
  useSavedNotesActions,
  useStudyReportQuery,
} from "@/lib/api/queries";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";
import { ExplainView } from "@/app/workspace/_components/ExplainView";
import { NotesView } from "@/app/workspace/_components/NotesView";
import { FlashcardsView } from "@/app/workspace/_components/FlashcardsView";
import { MemoryPalaceView } from "@/app/workspace/_components/MemoryPalaceView";
import { QuizBuilderView } from "@/app/workspace/_components/QuizBuilderView";
import { PdfReader } from "@/app/workspace/_components/PdfReader";
import { TutorPanel, READ_CHAT_SUGGESTIONS } from "@/app/workspace/_components/TutorPanel";
import { useStudyNav } from "@/app/workspace/_components/studyNav";
import { McqHeroPanel, McqReviewView } from "@/app/workspace/_components/McqPanels";
import { StudySourcePanel } from "@/app/workspace/_components/StudySourcePanel";
import { StudyEdgeTrigger } from "@/app/workspace/_components/StudyRails";
import { FloatingPanel } from "@/app/workspace/_components/FloatingPanel";
import { StudyMobileShell } from "@/app/workspace/_components/StudyMobileShell";
import { StudyMetaBar } from "@/app/workspace/_components/StudyMetaBar";
import {
  suggestNextPageRange,
  TestResultsScreen,
  DocumentCompleteScreen,
  StudyRangeReselectOverlay,
  PageCompleteInterstitial,
} from "@/app/workspace/_components/StudyScreens";
import { PageSelectionScreen, buildPageSliderMarks } from "@/app/workspace/_components/PageSelectionScreen";
import {
  STUDY_DESKTOP_BP,
  STUDY_COMPACT_BP,
  pagesInRange,
  type AnsweredCard,
} from "@/app/workspace/_components/studyLayout";
import { PetLoader } from "@/app/_components/PetLoader";
import { indexingStage, isTransientChatAssistantMessage } from "@/lib/constants";
import { getCachedPdfDocument, loadPdfForArtifact } from "@/lib/pdf";
import {
  normalizeMcqOptions,
  sanitizeMcqStem,
  type ArtifactMeta,
  type AssertionPayload,
  type McqGradeResponse,
  type McqState,
  type PagesInfo,
} from "@/lib/types";


export default function WorkspaceArtifactPage({
  params,
}: {
  params: Promise<{ artifactId: string }>;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { artifactId } = use(params);
  const invalidArtifactId = !isArtifactId(artifactId);
  const isLg = useMediaQuery(STUDY_DESKTOP_BP, false, { getInitialValueInEffect: true });
  const isCompact = useMediaQuery(STUDY_COMPACT_BP, false, { getInitialValueInEffect: true });
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  // Mode selection lives in the workspace layout so the global left sidebar can
  // host the mode navigator (below the source list) instead of a second rail.
  const { mode, setMode, setActive: setStudyNavActive } = useStudyNav();
  useEffect(() => {
    setStudyNavActive(true);
    return () => setStudyNavActive(false);
  }, [setStudyNavActive]);

  const [artifact, setArtifact] = useState<ArtifactMeta | null>(null);
  const [pages, setPages] = useState<PagesInfo | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [selectedPages, setSelectedPages] = useState<number[]>([]);
  const [lastClickedPage, setLastClickedPage] = useState<number | null>(null);
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [pdfLoading, setPdfLoading] = useState(false);
  const thumbCanvasRefs = useRef<Record<number, HTMLCanvasElement | null>>({});

  const [queue, setQueue] = useState<McqState | null>(null);
  const [question, setQuestion] = useState("Loading questions…");
  const [options, setOptions] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  // The concept the current question tests — captured per question for the report card.
  const [currentConcept, setCurrentConcept] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [gradeState, setGradeState] = useState<{ correct: boolean; correctIndex: number } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [mcqLoading, setMcqLoading] = useState(true);
  // Answered-question history + a "review" cursor (null = on the live question).
  const [answeredHistory, setAnsweredHistory] = useState<AnsweredCard[]>([]);
  const [reviewIndex, setReviewIndex] = useState<number | null>(null);

  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<{ role: string; content: string }[]>([]);
  const [chatBusy, setChatBusy] = useState(false);
  const chatAbortRef = useRef<AbortController | null>(null);
  const savedNotesQuery = useSavedNotesQuery(artifactId);
  const savedNotesActions = useSavedNotesActions(artifactId);
  const [readerNotesOpen, setReaderNotesOpen] = useState(false);
  const [readerMobileTab, setReaderMobileTab] = useState<"reader" | "buddy">("reader");
  const [sourceOpen, { open: openSource, close: closeSource }] = useDisclosure(false);
  const [tutorOpen, { open: openTutor, close: closeTutor }] = useDisclosure(false);
  // The floating Source/Tutor windows own their own geometry (see FloatingPanel),
  // so the workspace only needs the row ref to bound them.
  const studyRowRef = useRef<HTMLDivElement>(null);
  const [reselectOpen, setReselectOpen] = useState(false);

  const selectedRange = artifact?.meta?.selected_range;
  const studyRangeKey = selectedRange
    ? `${selectedRange.from}:${selectedRange.to}:${(selectedRange.pages ?? []).join(",")}`
    : "";
  const apiPageCount = pages?.page_count ?? artifact?.meta?.page_count ?? 1;
  const pageCount = Math.max(apiPageCount, pdfDoc?.numPages ?? 0, 1);
  const sortedSelection = useMemo(
    () => [...selectedPages].filter((p) => p >= 1 && p <= pageCount).sort((a, b) => a - b),
    [selectedPages, pageCount],
  );
  const sliderFrom = sortedSelection[0] ?? 1;
  const sliderTo =
    sortedSelection.length > 0 ? sortedSelection[sortedSelection.length - 1] : 1;
  const sliderMarks = useMemo(() => buildPageSliderMarks(pageCount), [pageCount]);

  const stem =
    mcqLoading
      ? "Loading questions…"
      : queue && !queue.current_assertion_id
        ? "Questions will appear once indexing finishes."
        : question;

  useEffect(() => {
    if (invalidArtifactId) {
      router.replace("/workspace");
    }
  }, [invalidArtifactId, router]);

  const artifactQuery = useArtifactQuery(artifactId, !invalidArtifactId);
  const pagesQuery = useArtifactPagesQuery(artifactId, !invalidArtifactId);
  const assertionQuery = useAssertionQuery(queue?.current_assertion_id);
  // Each mode (Read / Learn / Test) is its own conversation surface.
  const chatSurface = chatSurfaceForMode(mode);
  const chatMessagesQuery = useChatMessagesQuery(artifactId, mode, !invalidArtifactId);
  // Persistent report card — only fetched once a range is complete.
  const studyReportQuery = useStudyReportQuery(
    artifactId,
    Boolean(queue?.document_complete) && !invalidArtifactId,
  );
  const chatHydratedRef = useRef<string | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sync server query result into locally-editable state
    if (artifactQuery.data) setArtifact(artifactQuery.data);
    if (artifactQuery.error) {
      setSetupError(artifactQuery.error instanceof Error ? artifactQuery.error.message : "Could not load source");
    }
  }, [artifactQuery.data, artifactQuery.error]);

  useEffect(() => {
    if (pagesQuery.data) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- sync server pages into local state + reset selection
      setPages(pagesQuery.data);
      setSelectedPages([]);
      setLastClickedPage(null);
    }
  }, [pagesQuery.data]);

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  const queueRef = useRef(queue);
  queueRef.current = queue;

  // Switching conversation surface (mode) clears the view until the new thread loads.
  useEffect(() => {
    chatHydratedRef.current = null;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrate chat thread from server once per surface (chat is appended to locally during streaming)
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

  const isPdf = artifact?.content_type === "application/pdf";

  useEffect(() => {
    if (invalidArtifactId || !artifact || !isPdf) return;

    // eslint-disable-next-line react-hooks/set-state-in-effect -- async PDF document load — inherently an effect
    setPdfError(null);
    const cached = getCachedPdfDocument(artifactId);
    if (cached) {
      setPdfDoc(cached);
      setPdfLoading(false);
      return;
    }

    let cancelled = false;
    setPdfDoc(null);
    setPdfLoading(true);

    void (async () => {
      try {
        const pdf = await loadPdfForArtifact(artifactId, {
          url: `/api/documents/${artifactId}/file`,
          fetchBytes: () => apiFetchBytes(`/api/documents/${artifactId}/file`),
        });
        if (!cancelled) setPdfDoc(pdf);
      } catch (e) {
        if (!cancelled) setPdfError(e instanceof Error ? e.message : "Could not load PDF");
      } finally {
        if (!cancelled) setPdfLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- depend on artifact?.id (stable); the artifact object changes identity on every edit and would needlessly reload the PDF
  }, [artifactId, invalidArtifactId, isPdf, artifact?.id]);

  useEffect(() => {
    if (!pdfDoc?.numPages || pdfDoc.numPages <= 1) return;
    const n = pdfDoc.numPages;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- clamp the selection when the loaded PDF's page count changes
    setSelectedPages((prev) => prev.filter((p) => p <= n));
  }, [pdfDoc]);

  const studyPages = useMemo(() => {
    if (!selectedRange) return [];
    if (selectedRange.pages?.length) {
      return [...selectedRange.pages].sort((a, b) => a - b);
    }
    const pages: number[] = [];
    for (let p = selectedRange.from; p <= selectedRange.to; p += 1) pages.push(p);
    return pages;
  }, [selectedRange]);

  useEffect(() => {
    return () => {
      chatAbortRef.current?.abort();
    };
  }, []);

  const queueStreamRef = useRef<EventSource | null>(null);
  // Tracks whether the live SSE queue stream is connected. The fallback poll
  // below runs ONLY while the stream is down — a healthy stream already pushes
  // every queue update, so polling on top of it would just double-fetch.
  const [streamConnected, setStreamConnected] = useState(false);

  useEffect(() => {
    if (invalidArtifactId || !studyRangeKey || artifact?.status === "indexing") return;
    let cancelled = false;

    void (async () => {
      await ensureGuestSession();
      if (cancelled) return;
      try {
        const data = await apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`);
        if (cancelled) return;
        setQueue(data);
        setMcqLoading(false);
      } catch {
        /* stream below */
      }
      if (cancelled) return;

      queueStreamRef.current?.close();
      const url = apiUrl(`/api/artifacts/${artifactId}/learn-queue/stream`);
      const es = new EventSource(url, { withCredentials: true });
      queueStreamRef.current = es;
      es.onopen = () => {
        if (!cancelled) setStreamConnected(true);
      };
      es.addEventListener("queue", (ev) => {
        if (cancelled) return;
        try {
          const data = JSON.parse((ev as MessageEvent).data) as McqState;
          setQueue(data);
          setMcqLoading(false);
        } catch {
          /* ignore malformed */
        }
      });
      es.addEventListener("done", (ev) => {
        if (cancelled) return;
        try {
          const data = JSON.parse((ev as MessageEvent).data) as McqState;
          setQueue(data);
        } catch {
          /* ignore */
        }
        es.close();
        if (queueStreamRef.current === es) queueStreamRef.current = null;
        setStreamConnected(false);
      });
      es.addEventListener(
        "error",
        () => {
          if (cancelled) return;
          es.close();
          if (queueStreamRef.current === es) queueStreamRef.current = null;
          setStreamConnected(false);
          void (async () => {
            try {
              const data = await apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`);
              if (cancelled) return;
              setQueue(data);
            } catch {
              setQuestion("Sign in or reload to load questions.");
            } finally {
              if (!cancelled) setMcqLoading(false);
            }
          })();
        },
        { once: true },
      );
    })();

    return () => {
      cancelled = true;
      queueStreamRef.current?.close();
      queueStreamRef.current = null;
      setStreamConnected(false);
    };
  }, [artifactId, invalidArtifactId, studyRangeKey, artifact?.status]);

  useEffect(() => {
    if (invalidArtifactId || !studyRangeKey || artifact?.status !== "ready") return;
    // Fallback only: while the SSE stream is connected it already pushes every
    // update, so polling would double-fetch. Poll only when the stream is down.
    if (streamConnected) return;
    const needsPoll =
      !queue?.document_complete &&
      (!queue?.current_assertion_id ||
        (Boolean(queue.generation_pending) && (queue.pool_available ?? 0) === 0));
    if (!needsPoll) return;

    const id = window.setInterval(() => {
      void apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`)
        .then((data) => setQueue(data))
        .catch(() => {});
    }, 3000);
    return () => window.clearInterval(id);
  }, [
    artifactId,
    invalidArtifactId,
    studyRangeKey,
    artifact?.status,
    streamConnected,
    queue?.current_assertion_id,
    queue?.generation_pending,
    queue?.pool_available,
    queue?.document_complete,
  ]);

  useEffect(() => {
    if (invalidArtifactId || queue?.current_assertion_id) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- project the fetched assertion into the question/options view state
    setOptions([]);
    setSelected(null);
    setGradeState(null);
    setFeedback(null);
  }, [invalidArtifactId, queue?.current_assertion_id]);

  useEffect(() => {
    if (invalidArtifactId || !queue?.current_assertion_id) return;
    if (assertionQuery.isError) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentionally reset grade/feedback when the study mode changes
      setQuestion("Could not load question.");
      setOptions([]);
      return;
    }
    if (!assertionQuery.data) return;
    const row = assertionQuery.data;
    const p = (row.payload ?? {}) as AssertionPayload;
    setQuestion(sanitizeMcqStem(p.question ?? p.stem ?? row.title ?? "Question"));
    setOptions(normalizeMcqOptions(p.options, p.choices));
    setCurrentConcept((p.primary_concept ?? "").trim() || null);
    setSelected(null);
    setFeedback(null);
    setGradeState(null);
  }, [assertionQuery.data, assertionQuery.isError, queue?.current_assertion_id, invalidArtifactId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentionally reset grade/feedback on the active question change
    setGradeState(null);
    setFeedback(null);
  }, [mode]);

  useEffect(() => {
    if (!queue?.page_complete || queue.current_assertion_id || queue.document_complete) return;
    const id = window.setTimeout(() => {
      void refreshQueue();
    }, 1500);
    return () => window.clearTimeout(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- refreshQueue is recreated each render; listing it would reset this debounce timer every render
  }, [queue?.page_complete, queue?.current_assertion_id, queue?.document_complete]);

  async function refreshQueue() {
    const data = await apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`);
    setQueue(data);
  }

  async function setStudyMode(nextMode: "adaptive" | "classic") {
    if (queue?.study_mode === nextMode) return;
    // Optimistic — the change applies to the next question, no regeneration.
    setQueue((q) => (q ? { ...q, study_mode: nextMode } : q));
    try {
      await apiPost(`/api/artifacts/${artifactId}/study-mode`, { mode: nextMode });
    } catch {
      void refreshQueue();
    }
  }

  function handleMcqSelect(value: string) {
    if (gradeState && !gradeState.correct && mode === "learn") {
      setGradeState(null);
      setFeedback(null);
    }
    setSelected(value);
  }

  async function advanceMcq() {
    setSubmitting(true);
    try {
      await refreshQueue();
      setGradeState(null);
      setFeedback(null);
      setSelected(null);
    } catch {
      setFeedback("Could not load next question.");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitMcq() {
    if (!queue?.current_assertion_id || selected === null || submitting) return;
    setSubmitting(true);
    try {
      const res = await apiPost<McqGradeResponse>("/api/mcq/grade", {
        assertion_id: queue.current_assertion_id,
        choice_index: Number(selected),
        mode,
      });
      const correct = Boolean(res.correct);
      const correctIndex = res.correct_index ?? Number(selected);
      const gradedFeedback = res.feedback ?? (correct ? "Correct!" : "Try again.");
      setFeedback(gradedFeedback);
      setGradeState({ correct, correctIndex });
      // Record this answer so the learner can step back to review it later. Keep the
      // latest grade per question (learn-mode retries re-grade the same assertion).
      const answeredId = queue.current_assertion_id;
      const answeredSelection = Number(selected);
      setAnsweredHistory((h) => {
        // Preserve the FIRST-attempt result across Learn-mode retries — the report
        // card grades you on the first try, not the eventual retry success.
        const prior = h.find((c) => c.assertionId === answeredId);
        return [
          ...h.filter((c) => c.assertionId !== answeredId),
          {
            assertionId: answeredId,
            stem,
            options: [...options],
            selectedIndex: answeredSelection,
            gradeState: { correct, correctIndex },
            feedback: gradedFeedback,
            concept: currentConcept,
            firstTryCorrect: prior ? prior.firstTryCorrect : correct,
          },
        ];
      });
    } catch {
      setFeedback("Could not grade answer — try again.");
    } finally {
      setSubmitting(false);
    }
  }

  async function confirmRange() {
    if (sortedSelection.length === 0) {
      setSetupError("Select at least one page to study.");
      return;
    }
    setConfirming(true);
    setSetupError(null);
    try {
      const from = sortedSelection[0];
      const to = sortedSelection[sortedSelection.length - 1];
      await apiPost(`/api/artifacts/${artifactId}/page-range`, {
        from,
        to,
        pages: sortedSelection,
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.artifact(artifactId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.artifactPages(artifactId) });
      setReselectOpen(false);
      setQueue(null);
      setMcqLoading(true);
      setQuestion("Loading questions…");
      setOptions([]);
      setSelected(null);
      setGradeState(null);
      setFeedback(null);
      setAnsweredHistory([]);
      setReviewIndex(null);
      setArtifact(await apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`));
    } catch (e) {
      setSetupError(e instanceof Error ? e.message : "Could not start indexing");
    } finally {
      setConfirming(false);
    }
  }

  function openReselectPages() {
    if (!selectedRange) return;
    const suggestion = suggestNextPageRange(selectedRange, pageCount);
    setSelectedPages(pagesInRange(suggestion.from, suggestion.to));
    setLastClickedPage(suggestion.from);
    setSetupError(null);
    setReselectOpen(true);
  }

  function handleRangeChange(from: number, to: number) {
    const lo = Math.min(from, to);
    const hi = Math.max(from, to);
    setSelectedPages(pagesInRange(lo, hi));
    setLastClickedPage(lo);
  }

  function handlePageToggle(page: number, shiftKey: boolean) {
    setSelectedPages((prev) => {
      const next = new Set(prev);
      if (shiftKey && lastClickedPage !== null) {
        const lo = Math.min(lastClickedPage, page);
        const hi = Math.max(lastClickedPage, page);
        for (let p = lo; p <= hi; p += 1) next.add(p);
      } else if (next.has(page)) {
        next.delete(page);
      } else {
        next.add(page);
      }
      return [...next].sort((a, b) => a - b);
    });
    setLastClickedPage(page);
  }

  // Core streamer — assumes chatMessages already ends with the user turn + an empty
  // assistant placeholder to fill. Shared by send, regenerate, and edit-and-resend.
  async function runAssistant(userMsg: string) {
    setChatBusy(true);
    chatAbortRef.current?.abort();
    const abort = new AbortController();
    chatAbortRef.current = abort;
    try {
      await ensureGuestSession();
      // Scope the chat to the CURRENT mode. In Read mode the buddy is about the
      // document the user is reading — never the Learn queue's current page — so
      // it must not pin the learn page/question (that caused Read's "summarize"
      // to summarize the Learn page instead).
      const scope: Record<string, unknown> = { mode };
      if (mode !== "read") {
        const currentPage = queue?.current_page;
        if (currentPage && currentPage > 0) scope.current_page = currentPage;
        if (queue?.current_assertion_id) {
          scope.current_assertion_id = queue.current_assertion_id;
        }
        // The option the learner is on right now — even before checking — so the tutor
        // always knows which question and which choice they're considering.
        if (selected !== null) {
          scope.selected_choice_index = Number(selected);
        }
        if (gradeState !== null && selected !== null) {
          scope.confirmed_choice_index = Number(selected);
          scope.answer_correct = gradeState.correct;
        }
      }
      let assistant = "";
      let gotToken = false;
      await apiPostSSE("/api/chat", {
        document_id: artifactId,
        message: userMsg,
        scope,
      }, {
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
      }, { signal: abort.signal });
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
        ? `${ZIVO_ASSISTANT_NAME} is still waking up — try again in a moment.`
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
      /* best-effort — the panel is already cleared locally */
    }
    void queryClient.invalidateQueries({ queryKey: queryKeys.chatMessages(artifactId, chatSurface) });
  }

  // Read-mode (Study Buddy) helpers — quote a selection into the composer, ask the
  // buddy directly about a passage, and save answers/passages as notes linked to the doc.
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
    setReaderNotesOpen(true);
  }

  // Regenerate the most recent answer: drop the trailing assistant turn and re-ask
  // the last user message.
  function regenerateChat() {
    if (chatBusy || !chatContextReady) return;
    const lastUserIdx = chatMessages.map((x) => x.role).lastIndexOf("user");
    if (lastUserIdx === -1) return;
    const lastUser = chatMessages[lastUserIdx].content;
    setChatMessages([...chatMessages.slice(0, lastUserIdx + 1), { role: "assistant", content: "" }]);
    void runAssistant(lastUser);
  }

  // Edit a previous question: lift it back into the composer and trim the thread
  // from that point, so the user can tweak and resend (ChatGPT-style).
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

  if (invalidArtifactId) {
    return (
      <Center mih="50vh">
        <Stack gap="sm" w={280}>
          <Skeleton height={24} radius="md" />
          <Skeleton height={120} radius="md" />
        </Stack>
      </Center>
    );
  }

  if (setupError && !artifact) {
    return (
      <Center mih="50vh">
        <Stack align="center" gap="md" maw={420}>
          <Alert color="terracotta" title="Could not load source" variant="light">
            {setupError}
          </Alert>
          <Button variant="default" onClick={() => router.push("/workspace")}>
            Back to library
          </Button>
        </Stack>
      </Center>
    );
  }

  if (!artifact || !pages) {
    return (
      <Center mih="50vh">
        <Stack gap="sm" w={320}>
          <Skeleton height={28} width="60%" radius="md" />
          <Skeleton height={200} radius="md" />
          <Skeleton height={16} width="40%" radius="md" />
        </Stack>
      </Center>
    );
  }

  if (!selectedRange) {
    const shortName = artifact.filename?.replace(/\.[^.]+$/, "") ?? "Source";

    return (
      <PageSelectionScreen
        subtitle={`${shortName} · ${pageCount} pages`}
        pageCount={pageCount}
        sliderFrom={sliderFrom}
        sliderTo={sliderTo}
        sliderMarks={sliderMarks}
        selectedPages={sortedSelection}
        isDark={isDark}
        isPdf={isPdf}
        pdfDoc={pdfDoc}
        pdfLoading={pdfLoading}
        pdfError={pdfError}
        thumbCanvasRefs={thumbCanvasRefs}
        confirming={confirming}
        setupError={setupError}
        confirmLabel="Start studying"
        isCompact={isCompact}
        onRangeChange={handleRangeChange}
        onPageToggle={handlePageToggle}
        onSelectAll={() => {
          setSelectedPages(pagesInRange(1, pageCount));
          setLastClickedPage(1);
        }}
        onClearAll={() => {
          setSelectedPages([]);
          setLastClickedPage(null);
        }}
        onConfirm={() => void confirmRange()}
      />
    );
  }

  if (artifact.status === "failed") {
    return (
      <Center mih="70vh">
        <Paper withBorder p="xl" maw={480} w="100%">
          <Stack gap="md" align="center">
            <Title order={3} style={{ fontFamily: "var(--font-serif), Georgia, serif", fontWeight: 500 }}>
              Indexing failed
            </Title>
            <Text c="dimmed" ta="center" lh={1.6}>
              We could not finish preparing pages {selectedRange?.from}–{selectedRange?.to}. Try a smaller
              range or upload the file again.
            </Text>
            <Button variant="default" onClick={() => window.location.reload()}>
              Reload
            </Button>
          </Stack>
        </Paper>
      </Center>
    );
  }

  if (artifact.status === "indexing") {
    const progress = artifact.index_progress ?? 0;
    const stage = indexingStage(progress);

    return (
      <Center flex={1} px="sm">
        <Paper withBorder p={{ base: "lg", sm: "xl" }} maw={520} w="100%">
          <Stack gap="lg">
            <Stack gap="xs" align="center">
              <PetLoader size={64} variant="lavender" />
              <Text size="lg" fw={500} ta="center" style={{ letterSpacing: "-0.02em", fontFamily: "var(--font-serif), Georgia, serif" }}>
                {stage.title}
              </Text>
              <Text c="dimmed" ta="center" size="sm">
                {stage.detail}
              </Text>
              {artifact.filename ? (
                <Text size="xs" c="dimmed" ta="center" opacity={0.7}>
                  {artifact.filename}
                </Text>
              ) : null}
            </Stack>
            <Stack gap="xs">
              <Group justify="space-between">
                <Text size="sm" fw={500}>
                  Progress
                </Text>
                <Text size="sm" c="dimmed">
                  {progress}%
                </Text>
              </Group>
              <Progress value={progress} size="md" radius="xl" color="lavender" animated={progress < 100} />
            </Stack>
          </Stack>
        </Paper>
      </Center>
    );
  }

  const shortFilename = artifact.filename?.replace(/\.[^.]+$/, "") ?? "Source";
  const questionIndex = queue?.question_number ?? (queue?.questions_answered ?? 0) + 1;
  const questionTotal = queue?.question_budget ?? queue?.max_per_page ?? 0;
  const showPageComplete =
    Boolean(queue?.page_complete) && !queue?.current_assertion_id && !queue?.document_complete;
  const showDocumentComplete = Boolean(queue?.document_complete) && !reselectOpen;
  const showNoQuestions = Boolean(queue?.no_questions_reason) && !reselectOpen;
  // Test mode is summative: hold all feedback until the set is finished, then show
  // one score + a full review. (Learn keeps its encouraging per-page completion.)
  const testCorrect = answeredHistory.filter((c) => c.gradeState.correct).length;
  const showTestResults = mode === "test" && showDocumentComplete && answeredHistory.length > 0;
  const chatContextReady = queue?.rag_window_ready !== false;
  const completedRange = selectedRange;
  const nextRangeSuggestion = completedRange
    ? suggestNextPageRange(completedRange, pageCount)
    : null;

  const questionColumn = (
    <Box flex={1} mih={0} h="100%" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <StudyMetaBar
        questionIndex={questionIndex}
        questionTotal={questionTotal}
        page={queue?.current_page}
        mode={mode}
        onModeChange={setMode}
        showProgress={(mode === "learn" || mode === "test") && !mcqLoading && Boolean(queue?.current_assertion_id) && !showPageComplete && !showDocumentComplete}
        compact={isCompact}
        studyMode={queue?.study_mode}
        onStudyModeChange={(m) => void setStudyMode(m)}
      />
      <Box
        flex={1}
        mih={0}
        px={{ base: "sm", sm: "md", lg: "lg" }}
        pb={{ base: "xs", sm: "md" }}
        style={{
          display: "flex",
          flexDirection: "column",
          // Top-anchored (not centered) so revealing the explanation grows the card
          // downward instead of re-centering the whole panel — no layout jump.
          overflow: "hidden",
          justifyContent: "flex-start",
          paddingTop: "clamp(8px, 2vh, 20px)",
          minHeight: 0,
        }}
      >
        <Box
          maw={760}
          w="100%"
          mx="auto"
          mih={0}
          px={4}
          style={{
            flex: 1,
            maxHeight: "100%",
            overflowY: "auto",
            overflowX: "hidden",
            overscrollBehavior: "contain",
            scrollbarGutter: "stable",
          }}
        >
          {mode === "explain" ? (
            <ExplainView artifactId={artifact.id} compact={isCompact} />
          ) : mode === "notes" ? (
            <NotesView artifactId={artifact.id} compact={isCompact} />
          ) : mode === "cards" ? (
            <FlashcardsView artifactId={artifact.id} compact={isCompact} />
          ) : mode === "palace" ? (
            <MemoryPalaceView artifactId={artifact.id} compact={isCompact} />
          ) : mode === "quiz" ? (
            <QuizBuilderView artifactId={artifact.id} compact={isCompact} />
          ) : showNoQuestions ? (
            <Stack align="center" gap="sm" py="xl" ta="center">
              <Text ff="var(--font-serif)" fz={isCompact ? 22 : 28} fw={500} c="var(--mantine-color-text)">
                Nothing to quiz here
              </Text>
              <Text c="dimmed" maw={420}>
                This material doesn&rsquo;t contain testable content — it looks like a cover
                page, contents, or reference list. Choose different pages to study.
              </Text>
              <Button variant="light" color="lavender" radius="xl" mt="xs" onClick={openReselectPages}>
                Choose pages
              </Button>
            </Stack>
          ) : showTestResults ? (
            <TestResultsScreen
              correct={testCorrect}
              total={answeredHistory.length}
              answered={answeredHistory}
              report={studyReportQuery.data}
              compact={isCompact}
              canChoosePages={Boolean(completedRange)}
              onReview={() => setReviewIndex(0)}
              onChoosePages={openReselectPages}
            />
          ) : showDocumentComplete && completedRange ? (
            <DocumentCompleteScreen
              completedFrom={completedRange.from}
              completedTo={completedRange.to}
              pageCount={pageCount}
              bookFinished={nextRangeSuggestion?.bookFinished ?? false}
              nextFrom={nextRangeSuggestion?.from}
              nextTo={nextRangeSuggestion?.to}
              answered={answeredHistory}
              report={studyReportQuery.data}
              compact={isCompact}
              onChoosePages={openReselectPages}
            />
          ) : showPageComplete ? (
            <PageCompleteInterstitial
              page={queue?.current_page ?? 0}
              compact={isCompact}
              generating={Boolean(queue?.generation_pending)}
            />
          ) : reviewIndex !== null && answeredHistory[reviewIndex] ? (
          // Reviewing a previously-answered question — read-only, with the learner's
          // choice + the correct answer + explanation, and step controls.
          <Box style={{ height: "100%", minHeight: 0, width: "100%", overflow: "hidden" }}>
          <McqReviewView
            card={answeredHistory[reviewIndex]}
            index={reviewIndex}
            total={answeredHistory.length}
            compact={isCompact}
            onPrev={reviewIndex > 0 ? () => setReviewIndex(reviewIndex - 1) : undefined}
            onNext={() =>
              setReviewIndex(reviewIndex + 1 < answeredHistory.length ? reviewIndex + 1 : null)
            }
            onExit={() => setReviewIndex(null)}
          />
          </Box>
          ) : (
          // Fill the whole study area — the question is its own page: the stem stays
          // sticky at the top while options + explanation scroll beneath it.
          <Box style={{ height: "100%", minHeight: 0, width: "100%", overflow: "hidden" }}>
          <McqHeroPanel
            stem={stem}
            options={options}
            selected={selected}
            onSelect={handleMcqSelect}
            feedback={feedback}
            mcqLoading={mcqLoading}
            artifactStatus={artifact.status}
            indexProgress={artifact.index_progress}
            hasQuestion={Boolean(queue?.current_assertion_id)}
            queue={queue}
            mode={mode as "learn" | "test"}
            gradeState={gradeState}
            submitting={submitting}
            compact={isCompact}
            canReview={gradeState ? answeredHistory.length >= 2 : answeredHistory.length >= 1}
            onReviewPrevious={() =>
              setReviewIndex(gradeState ? answeredHistory.length - 2 : answeredHistory.length - 1)
            }
            onSubmit={() => void submitMcq()}
            onContinue={() => void advanceMcq()}
            onRetry={() => void refreshQueue()}
          />
          </Box>
          )}
        </Box>
      </Box>
    </Box>
  );

  if (mode === "read") {
    const notes = savedNotesQuery.data?.notes ?? [];
    // Stack reader/buddy into tabs whenever we're not in the desktop 3-pane (< 992),
    // matching how the rest of the study view drops to the single-column shell.
    const stacked = !isLg;
    return (
      <Box
        flex={1}
        mih={0}
        h="100%"
        pos="relative"
        mx={{ base: "calc(-1 * var(--mantine-spacing-xs))", sm: "calc(-1 * var(--mantine-spacing-md))" }}
        my={{ base: "calc(-1 * var(--mantine-spacing-xs))", sm: "calc(-1 * var(--mantine-spacing-md))" }}
        style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}
      >
        {/* Desktop 3-pane: the top was empty wasted space — float "Saved notes" into
            the corner so the reader + buddy use the full height. */}
        {!stacked && (
          <Button
            size="xs"
            variant={readerNotesOpen ? "light" : "default"}
            color="lavender"
            radius="xl"
            leftSection={<IconNotebook size={14} />}
            onClick={() => setReaderNotesOpen((o) => !o)}
            style={{ position: "absolute", top: 10, right: 16, zIndex: 20, boxShadow: "var(--mantine-shadow-sm)" }}
          >
            Saved notes{notes.length ? ` (${notes.length})` : ""}
          </Button>
        )}
        <Box
          flex={1}
          mih={0}
          h="100%"
          style={{ display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}
        >
        <StudyMetaBar
          questionIndex={0}
          questionTotal={0}
          mode={mode}
          onModeChange={setMode}
          showProgress={false}
          compact={isCompact}
        />
        {stacked ? (
          <Group justify="flex-end" px={{ base: "sm", sm: "md" }} pt={6} pb={6} style={{ flexShrink: 0 }}>
            <Button
              size="xs"
              variant={readerNotesOpen ? "light" : "subtle"}
              color="lavender"
              radius="xl"
              leftSection={<IconNotebook size={14} />}
              onClick={() => setReaderNotesOpen((o) => !o)}
            >
              Saved notes{notes.length ? ` (${notes.length})` : ""}
            </Button>
          </Group>
        ) : null}
        {stacked ? (
          <SegmentedControl
            fullWidth
            size="xs"
            radius="md"
            mx="sm"
            mb={6}
            value={readerMobileTab}
            onChange={(v) => setReaderMobileTab(v as "reader" | "buddy")}
            data={[
              { label: "Reader", value: "reader" },
              { label: "Study buddy", value: "buddy" },
            ]}
            style={{ flexShrink: 0 }}
          />
        ) : null}
        <Box flex={1} mih={0} style={{ display: "flex", overflow: "hidden" }}>
          {/* Reader: 75% column on desktop; full width with a tab toggle below 992. */}
          <Box
            flex={stacked ? undefined : 3}
            mih={0}
            w={stacked ? "100%" : undefined}
            style={{ overflow: "hidden", display: !stacked || readerMobileTab === "reader" ? "block" : "none" }}
          >
            <PdfReader
              artifactId={artifact.id}
              pdfDoc={pdfDoc}
              isPdf={isPdf}
              pageCount={pageCount}
              onQuote={(t) => { quoteToComposer(t); if (stacked) setReaderMobileTab("buddy"); }}
              onAsk={(m) => { askBuddy(m); if (stacked) setReaderMobileTab("buddy"); }}
              onSaveQuote={(t) => saveNote(t, t)}
            />
          </Box>
          <Box
            flex={stacked ? undefined : 1}
            mih={0}
            miw={stacked ? undefined : 300}
            w={stacked ? "100%" : undefined}
            style={{
              borderLeft: stacked ? undefined : "1px solid var(--app-border, var(--mantine-color-gray-2))",
              display: !stacked || readerMobileTab === "buddy" ? "flex" : "none",
              flexDirection: "column",
              maxWidth: stacked ? undefined : 460,
            }}
          >
            <TutorPanel
              messages={chatMessages}
              input={chatInput}
              busy={chatBusy}
              contextReady={chatContextReady}
              onInputChange={setChatInput}
              onSend={() => void sendChat()}
              onStop={stopChat}
              onRegenerate={regenerateChat}
              onEditUser={editChatFromUser}
              onClear={() => void clearChat()}
              onSaveNote={(c) => saveNote(c)}
              suggestions={READ_CHAT_SUGGESTIONS}
              emptyHint="Read on the left. Highlight anything to ask about it, or start here:"
              showHeader
            />
          </Box>
        </Box>
        <Drawer
          opened={readerNotesOpen}
          onClose={() => setReaderNotesOpen(false)}
          position="right"
          size="md"
          title={<Text ff="var(--font-serif)" fw={500} fz="lg">Saved notes</Text>}
        >
          {notes.length === 0 ? (
            <Text c="dimmed" fz="sm" ta="center" py="xl">
              Nothing saved yet. Select a passage or use “Save” on an answer to keep it here.
            </Text>
          ) : (
            <Stack gap="sm">
              {notes.map((n) => (
                <Paper key={n.id} withBorder radius="md" p="sm">
                  {n.quote ? (
                    <Text fz="xs" c="dimmed" fs="italic" mb={6} lineClamp={4} style={{ borderLeft: "2px solid var(--mantine-color-lavender-4)", paddingLeft: 8 }}>
                      {n.quote}
                    </Text>
                  ) : null}
                  <Text fz="sm" lh={1.55} style={{ whiteSpace: "pre-wrap" }}>{n.content}</Text>
                  <Group justify="flex-end" mt={6}>
                    <Button size="compact-xs" variant="subtle" color="gray" onClick={() => void savedNotesActions.remove(n.id)}>
                      Delete
                    </Button>
                  </Group>
                </Paper>
              ))}
            </Stack>
          )}
        </Drawer>
        </Box>
      </Box>
    );
  }

  return (
    <Box
      flex={1}
      mih={0}
      h="100%"
      mx={{ base: "calc(-1 * var(--mantine-spacing-xs))", sm: "calc(-1 * var(--mantine-spacing-md))" }}
      my={{ base: "calc(-1 * var(--mantine-spacing-xs))", sm: "calc(-1 * var(--mantine-spacing-md))" }}
      style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}
    >
      <Box
        flex={1}
        mih={0}
        h="100%"
        style={{ display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}
      >
      {isLg ? (
        <>
          <Box
            ref={studyRowRef}
            flex={1}
            mih={0}
            h="100%"
            pos="relative"
            style={{ display: "flex", overflow: "hidden", minHeight: 0 }}
          >
            {/* Base layer: the question column fills the row; the Source and Tutor
                windows float above it so the learner can keep answering. */}
            <Box flex={1} mih={0} pos="relative" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
              {questionColumn}
            </Box>

            {!sourceOpen && (
              <StudyEdgeTrigger
                side="left"
                icon={<IconFileText size={20} stroke={2} />}
                label="Source"
                color="lavender"
                onClick={openSource}
              />
            )}
            {!tutorOpen && (
              <StudyEdgeTrigger
                side="right"
                icon={<IconMessageCircle size={20} stroke={2} />}
                label={ZIVO_ASSISTANT_NAME}
                color="sage"
                onClick={openTutor}
              />
            )}

            <FloatingPanel
              open={sourceOpen}
              title={shortFilename}
              icon={<IconFileText size={16} stroke={2} />}
              accent="lavender"
              storageKey="zv-float-source"
              containerRef={studyRowRef}
              defaultSide="left"
              onClose={closeSource}
            >
              <StudySourcePanel
                filename={artifact.filename}
                pageRange={selectedRange}
                currentPage={queue?.current_page}
                isPdf={isPdf}
                pdfLoading={pdfLoading}
                pdfError={pdfError}
                pdfDoc={pdfDoc}
                studyPages={studyPages}
                open={sourceOpen}
              />
            </FloatingPanel>

            <FloatingPanel
              open={tutorOpen}
              title={ZIVO_ASSISTANT_NAME}
              icon={<IconMessageCircle size={16} stroke={2} />}
              accent="sage"
              storageKey="zv-float-tutor"
              containerRef={studyRowRef}
              defaultSide="right"
              onClose={closeTutor}
            >
              <TutorPanel
                messages={chatMessages}
                input={chatInput}
                busy={chatBusy}
                contextReady={chatContextReady}
                onInputChange={setChatInput}
                onSend={() => void sendChat()}
                onStop={stopChat}
                onRegenerate={regenerateChat}
                onEditUser={editChatFromUser}
                onClear={() => void clearChat()}
              />
            </FloatingPanel>
          </Box>
        </>
      ) : (
        <StudyMobileShell
          question={questionColumn}
          renderSource={(visible) => (
            <StudySourcePanel
              filename={artifact.filename}
              pageRange={selectedRange}
              currentPage={queue?.current_page}
              isPdf={isPdf}
              pdfLoading={pdfLoading}
              pdfError={pdfError}
              pdfDoc={pdfDoc}
              studyPages={studyPages}
              open={visible}
            />
          )}
          renderTutor={() => (
            <TutorPanel
              messages={chatMessages}
              input={chatInput}
              busy={chatBusy}
              contextReady={chatContextReady}
              onInputChange={setChatInput}
              onSend={() => void sendChat()}
              onStop={stopChat}
              onRegenerate={regenerateChat}
              onEditUser={editChatFromUser}
              onClear={() => void clearChat()}
              showHeader
            />
          )}
        />
      )}
      {reselectOpen && (
        <StudyRangeReselectOverlay
          filename={shortFilename}
          pageCount={pageCount}
          completedRange={completedRange}
          sliderFrom={sliderFrom}
          sliderTo={sliderTo}
          sliderMarks={sliderMarks}
          selectedPages={sortedSelection}
          isDark={isDark}
          isCompact={isCompact}
          isPdf={isPdf}
          pdfDoc={pdfDoc}
          thumbCanvasRefs={thumbCanvasRefs}
          confirming={confirming}
          setupError={setupError}
          bookFinished={nextRangeSuggestion?.bookFinished ?? false}
          onRangeChange={handleRangeChange}
          onPageToggle={handlePageToggle}
          onSelectAll={() => {
            setSelectedPages(pagesInRange(1, pageCount));
            setLastClickedPage(1);
          }}
          onClearAll={() => {
            setSelectedPages([]);
            setLastClickedPage(null);
          }}
          onClose={() => setReselectOpen(false)}
          onConfirm={() => void confirmRange()}
        />
      )}
      </Box>
    </Box>
  );
}
