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
} from "@mantine/core";
import { useDisclosure, useLocalStorage, useMediaQuery } from "@mantine/hooks";
import { IconFileText, IconMessageCircle, IconNotebook } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { apiFetchBytes, apiGet, apiPost, apiPostSSE, apiUrl, ensureGuestSession, isArtifactId } from "@/lib/api/client";
import {
  queryKeys,
  useArtifactPagesQuery,
  useArtifactQuery,
  useAssertionQuery,
  useBrainstormActions,
  useSavedNotesQuery,
  useSavedNotesActions,
  useStudyReportQuery,
} from "@/lib/api/queries";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";
import { BrainstormView } from "@/app/workspace/_components/BrainstormView";
import { ExplainView } from "@/app/workspace/_components/ExplainView";
import { NotesView } from "@/app/workspace/_components/NotesView";
import { FlashcardsView } from "@/app/workspace/_components/FlashcardsView";
import { MemoryPalaceView } from "@/app/workspace/_components/MemoryPalaceView";
import { QuizBuilderView } from "@/app/workspace/_components/QuizBuilderView";
import { InterviewView } from "@/app/workspace/_components/InterviewView";
import { MainsView } from "@/app/workspace/_components/MainsView";
import { CodingView } from "@/app/workspace/_components/CodingView";
import { ResumeView } from "@/app/workspace/_components/ResumeView";
import { ProgressView } from "@/app/workspace/_components/ProgressView";
import { PdfReader } from "@/app/workspace/_components/PdfReader";
import {
  TutorPanel,
  READ_CHAT_SUGGESTIONS,
  BRAINSTORM_SUGGESTIONS,
} from "@/app/workspace/_components/TutorPanel";
import { useStudyNav } from "@/app/workspace/_components/studyNav";
import { useTutorChat } from "@/app/workspace/_components/useTutorChat";
import { McqHeroPanel, McqReviewView } from "@/app/workspace/_components/McqPanels";
import { StudySourcePanel } from "@/app/workspace/_components/StudySourcePanel";
import { StudyEdgeTrigger } from "@/app/workspace/_components/StudyRails";
import { FloatingPanel } from "@/app/workspace/_components/FloatingPanel";
import { StudyMobileShell } from "@/app/workspace/_components/StudyMobileShell";
import { SelectionQuote } from "@/app/workspace/_components/SelectionQuote";
import { StudyMetaBar, type StudyAlign } from "@/app/workspace/_components/StudyMetaBar";
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
import { PetPlayground } from "@/app/_components/pets/PetPlayground";
import { indexingStage } from "@/lib/constants";
import { getCachedPdfDocument, loadPdfForArtifact } from "@/lib/pdf";
import { defaultWorkspaceMode } from "@/lib/studyPreferences";
import {
  normalizeMcqOptions,
  sanitizeMcqStem,
  type ArtifactMeta,
  type AssertionPayload,
  type McqState,
  type PagesInfo,
} from "@/lib/types";
import { useIsDark } from "@/lib/useIsDark";

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
  // Phone OR tablet study shell (<992): denser chrome. True phone (<768) also gets
  // the mode dropdown (tablet keeps the desktop sidebar mode list).
  const isNarrow = !isLg;
  const isDark = useIsDark();
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
  // Multi-select ("select all that apply") state: whether the current item is multi,
  // and which option indices are currently ticked.
  const [isMulti, setIsMulti] = useState(false);
  const [multiSelected, setMultiSelected] = useState<number[]>([]);
  // The concept the current question tests - captured per question for the report card.
  const [currentConcept, setCurrentConcept] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [gradeState, setGradeState] = useState<{ correct: boolean; correctIndex: number; correctIndices?: number[] } | null>(null);
  // Pin the graded card until Continue — SSE learn-queue advances current_assertion_id
  // as soon as the answer is recorded, which would otherwise wipe feedback mid-coach.
  const [pinnedAssertionId, setPinnedAssertionId] = useState<string | null>(null);
  const pinnedAssertionIdRef = useRef<string | null>(null);
  // Prefetched next id from grade/stream "next" event — Continue swaps without a full queue RT.
  const [pendingNextAssertionId, setPendingNextAssertionId] = useState<string | null>(null);
  const pendingNextAssertionIdRef = useRef<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [mcqLoading, setMcqLoading] = useState(true);
  // Answered-question history + a "review" cursor (null = on the live question).
  const [answeredHistory, setAnsweredHistory] = useState<AnsweredCard[]>([]);
  const [reviewIndex, setReviewIndex] = useState<number | null>(null);
  const [flagBusy, setFlagBusy] = useState(false);
  const [flaggedIds, setFlaggedIds] = useState<Record<string, true>>({});

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
  // Bumped when the learner quotes selected MCQ text into chat, so the mobile
  // shell can jump to the tutor tab (desktop just opens the floating panel).
  const [mobileTutorFocus, setMobileTutorFocus] = useState(0);

  useEffect(() => {
    if (mode === "test") closeTutor();
  }, [mode, closeTutor]);

  // Study-column alignment (desktop): centred, or pinned left beside the source panel.
  const [studyAlign, setStudyAlign] = useLocalStorage<StudyAlign>({
    key: "zv-study-align",
    defaultValue: "center",
    getInitialValueInEffect: false,
  });

  // Tutor chat (per-mode conversation: hydrate, stream, regenerate, clear, plus
  // Read-mode Study-Buddy quote/ask/save) lives in its own hook.
  const {
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
  } = useTutorChat({
    artifactId,
    mode,
    enabled: !invalidArtifactId,
    queue,
    selected,
    multiSelected,
    isMulti,
    gradeState,
    savedNotesActions,
    onSavedNote: () => setReaderNotesOpen(true),
  });

  // Same page component instance survives sidebar hops A → B — wipe mirrored local
  // state so B never flashes A's filename, queue, or answer history. Also reset mode
  // so a Coding/Resume session on A doesn't open B in the wrong surface.
  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect -- artifactId change must reset local study state without remounting the route */
    setArtifact(null);
    setPages(null);
    setSetupError(null);
    setConfirming(false);
    setSelectedPages([]);
    setLastClickedPage(null);
    setPdfDoc(null);
    setPdfError(null);
    setPdfLoading(false);
    thumbCanvasRefs.current = {};
    setQueue(null);
    setQuestion("Loading questions…");
    setOptions([]);
    setSelected(null);
    setIsMulti(false);
    setMultiSelected([]);
    setCurrentConcept(null);
    setFeedback(null);
    setGradeState(null);
    pinnedAssertionIdRef.current = null;
    setPinnedAssertionId(null);
    pendingNextAssertionIdRef.current = null;
    setPendingNextAssertionId(null);
    setSubmitting(false);
    setMcqLoading(true);
    setAnsweredHistory([]);
    setReviewIndex(null);
    setMode(defaultWorkspaceMode());
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [artifactId, setMode]);

  // Brainstorm turns the same tutor panel into the ideation partner: the ANGLES each
  // reply ends with become chips you can explore (send as the next turn) or keep (pin
  // to the idea board). Spread into every TutorPanel so desktop and mobile match.
  const brainstormActions = useBrainstormActions(artifactId);
  const brainstormChatProps =
    mode === "brainstorm"
      ? {
          suggestions: BRAINSTORM_SUGGESTIONS,
          emptyHint:
            "Think out loud about this source. Every reply ends with three angles you could pull.",
          onExploreAngle: (angle: string) => askBuddy(angle),
          onKeepAngle: (angle: string) => void brainstormActions.keep(angle),
        }
      : {};

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

  const displayAssertionId = pinnedAssertionId ?? queue?.current_assertion_id ?? null;
  const stem =
    mcqLoading
      ? "Loading questions…"
      : queue && !displayAssertionId
        ? "Questions will appear once indexing finishes."
        : question;

  useEffect(() => {
    if (invalidArtifactId) {
      router.replace("/workspace");
    }
  }, [invalidArtifactId, router]);

  const artifactQuery = useArtifactQuery(artifactId, !invalidArtifactId);
  const pagesQuery = useArtifactPagesQuery(artifactId, !invalidArtifactId);
  const assertionQuery = useAssertionQuery(displayAssertionId);
  // Persistent report card - only fetched once a range is complete.
  const studyReportQuery = useStudyReportQuery(
    artifactId,
    Boolean(queue?.document_complete) && !invalidArtifactId,
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sync server query result into locally-editable state
    if (artifactQuery.data) setArtifact(artifactQuery.data);
    if (artifactQuery.error) {
      setSetupError(artifactQuery.error instanceof Error ? artifactQuery.error.message : "Could not load source");
    }
  }, [artifactQuery.data, artifactQuery.error]);

  useEffect(() => {
    if (!pagesQuery.data) return;
    const data = pagesQuery.data;
    const count = Math.max(data.page_count ?? 1, 1);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sync server pages; default/clamp selection (artifactId reset already clears)
    setPages(data);
    if (artifact?.meta?.selected_range) return;
    setSelectedPages((prev) => {
      const clamped = prev.filter((p) => p >= 1 && p <= count);
      if (clamped.length > 0) return clamped;
      if (count === 1) return [1];
      return [];
    });
    setLastClickedPage((prev) => {
      if (prev !== null && prev >= 1 && prev <= count) return prev;
      return count === 1 ? 1 : null;
    });
  }, [pagesQuery.data, artifact?.meta?.selected_range]);

  // Explicit indexing → ready poll. A freshly-uploaded PDF first settles the
  // artifact query on status "pending" (awaiting page selection); React Query
  // does not reliably (re)arm a refetchInterval that was previously false once
  // the status transitions to "indexing" after the learner confirms pages - so
  // the full-screen "indexing N%" loader would freeze at its last sampled value
  // until a manual refresh. Own the poll here so it always runs while indexing
  // and stops the instant the document is ready.
  useEffect(() => {
    if (invalidArtifactId || artifact?.status !== "indexing") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`);
        if (cancelled) return;
        setArtifact(data);
        queryClient.setQueryData(queryKeys.artifact(artifactId), data);
      } catch {
        /* transient - keep polling */
      }
    };
    const id = window.setInterval(() => void poll(), 2500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [invalidArtifactId, artifactId, artifact?.status, queryClient]);

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  const queueRef = useRef(queue);
  queueRef.current = queue;

  const isPdf = artifact?.content_type === "application/pdf";

  useEffect(() => {
    if (invalidArtifactId || !artifact || !isPdf) return;

    // eslint-disable-next-line react-hooks/set-state-in-effect -- async PDF document load - inherently an effect
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

  const queueStreamRef = useRef<EventSource | null>(null);
  // Tracks whether the live SSE queue stream is connected. The fallback poll
  // below runs ONLY while the stream is down - a healthy stream already pushes
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
          setQueue((prev) => {
            // While graded feedback is pinned, keep showing the answered card —
            // still absorb pool/progress fields so Continue stays warm.
            const pinned = pinnedAssertionIdRef.current;
            if (pinned && prev) {
              return {
                ...data,
                current_assertion_id: pinned,
              };
            }
            return data;
          });
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
        // The RAG window slides as the learner advances pages, resetting
        // rag_window_ready to false until the new pages finish indexing. Keep
        // polling until it flips back so the chat composer re-enables on its own
        // instead of being stuck on "Preparing chat context…" until a refresh.
        queue.rag_window_ready === false ||
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
    queue?.rag_window_ready,
  ]);

  useEffect(() => {
    if (invalidArtifactId) return;
    if (queue?.current_assertion_id) {
      // While pinned on a graded card, ignore SSE advances that would clear
      // feedback before the learner hits Continue.
      if (pinnedAssertionIdRef.current) return;
      // Assertion advanced: reset answer chrome only. Keep stem/options on screen
      // until the next fetch lands — clearing them made hasQuestion false and
      // McqHeroPanel flashed the full loading ring for ~200–500ms.
      setSelected(null);
      setMultiSelected([]);
      setGradeState(null);
      setFeedback(null);
      return;
    }
    if (pinnedAssertionIdRef.current) return;
    setOptions([]);
    setSelected(null);
    setGradeState(null);
    setFeedback(null);
  }, [invalidArtifactId, queue?.current_assertion_id]);

  useEffect(() => {
    if (invalidArtifactId || !displayAssertionId) return;
    if (assertionQuery.isError) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- intentionally reset grade/feedback when the study mode changes
      setQuestion("Could not load question.");
      setOptions([]);
      return;
    }
    if (!assertionQuery.data || assertionQuery.isPlaceholderData) return;
    // Don't clobber the graded card while pinned — assertion payload is already on screen.
    if (pinnedAssertionIdRef.current && pinnedAssertionIdRef.current === displayAssertionId) {
      return;
    }
    const row = assertionQuery.data;
    const p = (row.payload ?? {}) as AssertionPayload;
    setQuestion(sanitizeMcqStem(p.question ?? p.stem ?? row.title ?? "Question"));
    setOptions(normalizeMcqOptions(p.options, p.choices));
    setCurrentConcept((p.primary_concept ?? "").trim() || null);
    setIsMulti(Array.isArray(p.correct_indices) && p.correct_indices.length >= 2);
    setMultiSelected([]);
    setSelected(null);
    setFeedback(null);
    setGradeState(null);
  }, [assertionQuery.data, assertionQuery.isError, assertionQuery.isPlaceholderData, displayAssertionId, invalidArtifactId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentionally reset grade/feedback on the active question change
    setGradeState(null);
    setFeedback(null);
  }, [mode]);

  useEffect(() => {
    if (!queue?.page_complete || queue.current_assertion_id || queue.document_complete) return;
    // Short debounce only — was 1.5s of dead air after the last question on a page.
    const id = window.setTimeout(() => {
      void refreshQueue();
    }, 200);
    return () => window.clearTimeout(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- refreshQueue is recreated each render; listing it would reset this debounce timer every render
  }, [queue?.page_complete, queue?.current_assertion_id, queue?.document_complete]);

  async function refreshQueue() {
    const data = await apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`);
    if (pinnedAssertionIdRef.current) {
      setQueue({ ...data, current_assertion_id: pinnedAssertionIdRef.current });
    } else {
      setQueue(data);
    }
  }

  async function setStudyMode(nextMode: "adaptive" | "classic") {
    if (queue?.study_mode === nextMode) return;
    // Optimistic - the change applies to the next question, no regeneration.
    setQueue((q) => (q ? { ...q, study_mode: nextMode } : q));
    try {
      await apiPost(`/api/artifacts/${artifactId}/study-mode`, { mode: nextMode });
    } catch {
      void refreshQueue();
    }
  }

  // Surface the tutor composer (floating panel on desktop, tutor tab on mobile)
  // so a quoted passage is immediately visible and editable.
  function revealTutor() {
    if (isLg) openTutor();
    else setMobileTutorFocus((n) => n + 1);
  }
  function quoteSelectionToChat(text: string) {
    quoteToComposer(text);
    revealTutor();
  }
  function explainSelectionInChat(text: string) {
    setChatInput(`Explain this in simple terms:\n\n"${text}"\n\n`);
    revealTutor();
  }

  function handleMcqSelect(value: string) {
    if (gradeState && !gradeState.correct && mode === "learn") {
      setGradeState(null);
      setFeedback(null);
    }
    setSelected(value);
  }

  function handleMcqToggle(index: number) {
    // In Learn mode, editing the selection after a wrong grade clears the verdict
    // so the learner can retry (mirrors handleMcqSelect).
    if (gradeState && !gradeState.correct && mode === "learn") {
      setGradeState(null);
      setFeedback(null);
    }
    setMultiSelected((prev) =>
      prev.includes(index) ? prev.filter((i) => i !== index) : [...prev, index].sort((a, b) => a - b),
    );
  }

  async function advanceMcq() {
    setSubmitting(true);
    try {
      const nextId = pendingNextAssertionIdRef.current;
      pinnedAssertionIdRef.current = null;
      setPinnedAssertionId(null);
      pendingNextAssertionIdRef.current = null;
      setPendingNextAssertionId(null);
      setGradeState(null);
      setFeedback(null);
      setSelected(null);
      setMultiSelected([]);
      if (nextId) {
        // Instant Next: swap to the prefetched card; background-refresh pool metadata.
        setQueue((q) => (q ? { ...q, current_assertion_id: nextId } : q));
        void apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`)
          .then((data) => {
            if (pinnedAssertionIdRef.current) return;
            setQueue(data);
          })
          .catch(() => {});
      } else {
        await refreshQueue();
      }
    } catch {
      setFeedback("Could not load next question.");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitMcq() {
    const hasSelection = isMulti ? multiSelected.length > 0 : selected !== null;
    if (!displayAssertionId || !hasSelection || submitting) return;
    setSubmitting(true);
    setFeedback(null);
    const answeredId = displayAssertionId;
    const answeredSelection = isMulti ? (multiSelected[0] ?? -1) : Number(selected);
    // Snapshot stem/options/selection at submit time — queue can advance before SSE
    // finishes, and a stale render closure would pair the wrong text with answeredId.
    const stemSnapshot = question;
    const optionsSnapshot = [...options];
    const conceptSnapshot = currentConcept;
    const multiSnapshot = isMulti ? [...multiSelected] : undefined;
    // Pin immediately so learn-queue SSE cannot wipe the card mid-grade.
    pinnedAssertionIdRef.current = answeredId;
    setPinnedAssertionId(answeredId);
    // Verdict-first stream: the outcome (index compare + stored explanation) lands
    // in ~200ms and reveals immediately; the LLM coaching follows as a second event.
    let gotVerdict = false;
    let verdictExplanation = "";
    try {
      await apiPostSSE(
        "/api/mcq/grade/stream",
        {
          assertion_id: answeredId,
          // choice_index carries the (first) chosen option for both paths; multi
          // items also send the full chosen set via choice_indices.
          choice_index: isMulti ? (multiSelected[0] ?? -1) : Number(selected),
          ...(isMulti ? { choice_indices: multiSelected } : {}),
          mode,
        },
        {
          onEvent: (event, data) => {
            if (event === "verdict") {
              let v: { correct?: boolean; correct_index?: number; correct_indices?: number[]; explanation?: string };
              try {
                v = JSON.parse(data);
              } catch {
                return;
              }
              gotVerdict = true;
              verdictExplanation = v.explanation ?? "";
              const correct = Boolean(v.correct);
              const correctIndex = v.correct_index ?? (isMulti ? (multiSelected[0] ?? 0) : Number(selected));
              const correctIndices = v.correct_indices;
              // Reveal the outcome now - coaching is still on its way.
              setGradeState({ correct, correctIndex, correctIndices });
              setSubmitting(false);
              // Record the answer immediately (coaching text is patched in below).
              setAnsweredHistory((h) => {
                // Preserve the FIRST-attempt result across Learn-mode retries - the
                // report card grades you on the first try, not the eventual retry.
                const prior = h.find((c) => c.assertionId === answeredId);
                return [
                  ...h.filter((c) => c.assertionId !== answeredId),
                  {
                    assertionId: answeredId,
                    stem: stemSnapshot,
                    options: optionsSnapshot,
                    selectedIndex: answeredSelection,
                    selectedIndices: multiSnapshot,
                    gradeState: { correct, correctIndex, correctIndices },
                    feedback: verdictExplanation,
                    concept: conceptSnapshot,
                    firstTryCorrect: prior ? prior.firstTryCorrect : correct,
                  },
                ];
              });
            } else if (event === "next") {
              let n: { next_assertion_id?: string };
              try {
                n = JSON.parse(data);
              } catch {
                return;
              }
              const nextId = n.next_assertion_id;
              if (!nextId) return;
              pendingNextAssertionIdRef.current = nextId;
              setPendingNextAssertionId(nextId);
              // Prefetch stem+options so Continue is one-frame instant.
              void queryClient.prefetchQuery({
                queryKey: queryKeys.assertion(nextId),
                queryFn: () =>
                  apiGet<{ payload: Record<string, unknown>; title?: string }>(
                    `/api/assertions/${nextId}`,
                  ),
                staleTime: 60_000,
              });
            } else if (event === "feedback") {
              let f: { feedback?: string };
              try {
                f = JSON.parse(data);
              } catch {
                return;
              }
              const text = (f.feedback || "").trim() || verdictExplanation;
              if (!text) return;
              setFeedback(text);
              setAnsweredHistory((h) =>
                h.map((c) => (c.assertionId === answeredId ? { ...c, feedback: text } : c)),
              );
            }
          },
        },
      );
      // Stream ended without ever producing coaching - fall back so the "writing…"
      // affordance resolves instead of hanging.
      setFeedback((prev) => prev ?? (verdictExplanation || "Answer recorded."));
    } catch {
      if (!gotVerdict) {
        pinnedAssertionIdRef.current = null;
        setPinnedAssertionId(null);
        setFeedback("Could not grade answer - try again.");
      } else setFeedback((prev) => prev ?? (verdictExplanation || "Answer recorded."));
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
      const updated = await apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`);
      queryClient.setQueryData(queryKeys.artifact(artifactId), updated);
      setArtifact(updated);
      void queryClient.invalidateQueries({ queryKey: queryKeys.artifactPages(artifactId) });
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
        subtitle={`${shortName} · ${pageCount === 1 ? "1 page" : `${pageCount} pages`}`}
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
        isCompact={isNarrow}
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
              We could not finish preparing pages {selectedRange?.from}-{selectedRange?.to}. Try a smaller
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
              <PetPlayground height={130} count={1} wander style={{ width: 400, maxWidth: "100%" }} />
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
  const showPromptReselect = Boolean(queue?.prompt_reselect_pages) && !reselectOpen;
  const reselectPromptCopy =
    queue?.prompt_reselect_reason === "unreadable_content"
      ? {
          title: "We can see content — but can't study it",
          body: "This looks full, but we can't study it as text. Choose pages with selectable text.",
        }
      : {
          title: "These pages look empty",
          body: "Several pages here have no readable content. See content on these pages, or are they blank? Pick only pages with actual text to study.",
        };
  // Test mode is summative: hold all feedback until the set is finished, then show
  // one score + a full review. (Learn keeps its encouraging per-page completion.)
  const testCorrect = answeredHistory.filter((c) => c.gradeState.correct).length;
  const showTestResults = mode === "test" && showDocumentComplete && answeredHistory.length > 0;
  const completedRange = selectedRange;
  const nextRangeSuggestion = completedRange
    ? suggestNextPageRange(completedRange, pageCount)
    : null;

  // Both edges carry a floating trigger (SOURCE on the left, Zivo on the right), so a
  // pinned column has to leave room for one or it slides underneath.
  const pinned = isNarrow ? null : studyAlign === "left" ? "left" : studyAlign === "right" ? "right" : null;
  const questionColumn = (
    <Box flex={1} mih={0} h="100%" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <StudyMetaBar
        questionIndex={questionIndex}
        questionTotal={questionTotal}
        page={queue?.current_page}
        mode={mode}
        onModeChange={setMode}
        showProgress={(mode === "learn" || mode === "test") && !mcqLoading && Boolean(displayAssertionId) && !showPageComplete && !showDocumentComplete}
        compact={isNarrow}
        showModeSelect={isCompact}
        studyMode={queue?.study_mode}
        onStudyModeChange={(m) => void setStudyMode(m)}
        align={studyAlign}
        onAlignChange={setStudyAlign}
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
          // downward instead of re-centering the whole panel - no layout jump.
          overflow: "hidden",
          justifyContent: "flex-start",
          paddingTop: "clamp(8px, 2vh, 20px)",
          minHeight: 0,
        }}
      >
        <Box
          maw={760}
          w="100%"
          mih={0}
          px={4}
          style={{
            // Phones always centre (the column already fills the width); on desktop the
            // learner chooses, and the choice is remembered across sessions. The 48px
            // keeps a pinned column clear of that edge's floating trigger, which is
            // absolutely positioned over the study area - without it the option cards
            // slide underneath.
            ...(pinned === "left"
              ? { marginLeft: 48, marginRight: 0 }
              : pinned === "right"
                ? { marginLeft: "auto", marginRight: 48 }
                : { marginInline: "auto" }),
            flex: 1,
            maxHeight: "100%",
            overflowY: "auto",
            overflowX: "hidden",
            overscrollBehavior: "contain",
            scrollbarGutter: "stable",
          }}
        >
          {mode === "brainstorm" ? (
            <BrainstormView artifactId={artifact.id} compact={isNarrow} />
          ) : mode === "explain" ? (
            <ExplainView artifactId={artifact.id} compact={isNarrow} />
          ) : mode === "notes" ? (
            <NotesView artifactId={artifact.id} compact={isNarrow} />
          ) : mode === "cards" ? (
            <FlashcardsView artifactId={artifact.id} compact={isNarrow} />
          ) : mode === "palace" ? (
            <MemoryPalaceView artifactId={artifact.id} compact={isNarrow} />
          ) : mode === "quiz" ? (
            <QuizBuilderView artifactId={artifact.id} compact={isNarrow} />
          ) : mode === "interview" ? (
            <InterviewView artifactId={artifact.id} compact={isNarrow} />
          ) : mode === "mains" ? (
            <MainsView artifactId={artifact.id} compact={isNarrow} />
          ) : mode === "coding" ? (
            <CodingView artifactId={artifact.id} compact={isNarrow} />
          ) : mode === "resume" ? (
            <ResumeView artifactId={artifact.id} compact={isNarrow} />
          ) : mode === "progress" ? (
            <ProgressView
              artifactId={artifact.id}
              compact={isNarrow}
              onStartLearn={() => setMode("learn")}
              onStudyConcept={(concept) => {
                void apiPost(`/api/artifacts/${artifact.id}/focus-concept`, { concept }).finally(() => {
                  setMode("learn");
                  void refreshQueue();
                });
              }}
            />
          ) : showPromptReselect ? (
            <Stack align="center" gap="sm" py="xl" ta="center">
              <Text ff="var(--font-serif)" fz={isNarrow ? 22 : 28} fw={500} c="var(--mantine-color-text)">
                {reselectPromptCopy.title}
              </Text>
              <Text c="dimmed" maw={420}>
                {reselectPromptCopy.body}
              </Text>
              <Button variant="light" color="lavender" radius="xl" mt="xs" onClick={openReselectPages}>
                Choose pages
              </Button>
            </Stack>
          ) : showNoQuestions ? (
            <Stack align="center" gap="sm" py="xl" ta="center">
              <Text ff="var(--font-serif)" fz={isNarrow ? 22 : 28} fw={500} c="var(--mantine-color-text)">
                Nothing to quiz here
              </Text>
              <Text c="dimmed" maw={420}>
                This material doesn&rsquo;t contain testable content - it looks like a cover
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
              compact={isNarrow}
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
              compact={isNarrow}
              onChoosePages={openReselectPages}
            />
          ) : showPageComplete ? (
            <PageCompleteInterstitial
              page={queue?.current_page ?? 0}
              compact={isNarrow}
              generating={Boolean(queue?.generation_pending)}
            />
          ) : reviewIndex !== null && answeredHistory[reviewIndex] ? (
          // Reviewing a previously-answered question - read-only, with the learner's
          // choice + the correct answer + explanation, and step controls.
          <Box style={{ height: "100%", minHeight: 0, width: "100%", overflow: "hidden" }}>
          <McqReviewView
            card={answeredHistory[reviewIndex]}
            index={reviewIndex}
            total={answeredHistory.length}
            compact={isNarrow}
            onPrev={reviewIndex > 0 ? () => setReviewIndex(reviewIndex - 1) : undefined}
            onNext={() =>
              setReviewIndex(reviewIndex + 1 < answeredHistory.length ? reviewIndex + 1 : null)
            }
            onExit={() => setReviewIndex(null)}
          />
          </Box>
          ) : (
          // Fill the whole study area - the question is its own page: the stem stays
          // sticky at the top while options + explanation scroll beneath it.
          <Box style={{ height: "100%", minHeight: 0, width: "100%", overflow: "hidden" }}>
          <SelectionQuote
            onAsk={quoteSelectionToChat}
            onExplain={explainSelectionInChat}
            disabled={mcqLoading || !displayAssertionId}
          >
          <McqHeroPanel
            stem={stem}
            options={options}
            selected={selected}
            onSelect={handleMcqSelect}
            multiSelect={isMulti}
            selectedIndices={multiSelected}
            onToggle={handleMcqToggle}
            feedback={feedback}
            mcqLoading={mcqLoading}
            artifactStatus={artifact.status}
            indexProgress={artifact.index_progress}
            hasQuestion={Boolean(displayAssertionId) && options.length > 0}
            queue={queue}
            mode={mode as "learn" | "test"}
            gradeState={gradeState}
            submitting={submitting}
            compact={isNarrow}
            canReview={gradeState ? answeredHistory.length >= 2 : answeredHistory.length >= 1}
            onReviewPrevious={() =>
              setReviewIndex(gradeState ? answeredHistory.length - 2 : answeredHistory.length - 1)
            }
            onSubmit={() => void submitMcq()}
            onContinue={() => void advanceMcq()}
            onRetry={() => void refreshQueue()}
            flagged={Boolean(displayAssertionId && flaggedIds[displayAssertionId])}
            flagBusy={flagBusy}
            onFlagQuestion={
              displayAssertionId
                ? (reason) => {
                    const aid = displayAssertionId;
                    setFlagBusy(true);
                    void apiPost(`/api/assertions/${aid}/flag`, { reason })
                      .then(() => setFlaggedIds((m) => ({ ...m, [aid]: true })))
                      .catch(() => undefined)
                      .finally(() => setFlagBusy(false));
                  }
                : undefined
            }
          />
          </SelectionQuote>
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
        {/* Desktop 3-pane: the top was empty wasted space - float "Saved notes" into
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
          compact={isNarrow}
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
            {mode !== "test" && !tutorOpen && (
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

            {mode !== "test" ? (
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
                {...brainstormChatProps}
              />
            </FloatingPanel>
            ) : null}
          </Box>
        </>
      ) : (
        <StudyMobileShell
          focusTutorKey={mobileTutorFocus}
          tutorHidden={mode === "test"}
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
              {...brainstormChatProps}
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
          isCompact={isNarrow}
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
