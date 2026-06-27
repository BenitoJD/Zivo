"use client";

import {
  use,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type WheelEvent,
} from "react";
import { useRouter } from "next/navigation";
import {
  ActionIcon,
  Alert,
  Box,
  Button,
  Center,
  Drawer,
  Group,
  Loader,
  Menu,
  NumberInput,
  Paper,
  Progress,
  RangeSlider,
  ScrollArea,
  SegmentedControl,
  Skeleton,
  Stack,
  Text,
  Textarea,
  ThemeIcon,
  Title,
  Tooltip,
  UnstyledButton,
  useMantineColorScheme,
} from "@mantine/core";
import { useDisclosure, useHover, useInterval, useLocalStorage, useMediaQuery, useMounted } from "@mantine/hooks";
import {
  IconArrowDown,
  IconArrowLeft,
  IconArrowRight,
  IconArrowUp,
  IconAdjustmentsHorizontal,
  IconArrowsMaximize,
  IconBulb,
  IconCheck,
  IconChevronDown,
  IconClipboardList,
  IconFileText,
  IconGripVertical,
  IconHistory,
  IconMessageCircle,
  IconNotebook,
  IconPencil,
  IconPlayerStop,
  IconPoint,
  IconRefresh,
  IconX,
  IconZoomIn,
  IconZoomOut,
} from "@tabler/icons-react";
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
} from "@/lib/api/queries";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";
import { BrandMark } from "@/app/_components/BrandMark";
import { ExplainView } from "@/app/workspace/_components/ExplainView";
import { NotesView } from "@/app/workspace/_components/NotesView";
import { FlashcardsView } from "@/app/workspace/_components/FlashcardsView";
import { MemoryPalaceView } from "@/app/workspace/_components/MemoryPalaceView";
import { QuizBuilderView } from "@/app/workspace/_components/QuizBuilderView";
import { PdfReader } from "@/app/workspace/_components/PdfReader";
import { GenerationStages } from "@/app/workspace/_components/GenerationStages";
import { useStudyNav, type StudyMode } from "@/app/workspace/_components/studyNav";
import { PetLoader } from "@/app/_components/PetLoader";
import { mcqOptionChrome, McqFeedbackCard } from "@/app/_components/mcq/McqCard";
import { AssistantMarkdown, MessageCopyAction } from "@/lib/chatMarkdown";
import { indexingStage, isTransientChatAssistantMessage } from "@/lib/constants";
import { learnWaitStatus } from "@/lib/learnStatus";
import {
  bucketPdfThumbWidth,
  cancelAllPdfRenders,
  clampPdfScroll,
  getCachedPdfDocument,
  loadPdfForArtifact,
  pdfDisplayHeight,
  pdfPageAspectRatio,
  pdfPageFitScale,
  renderPdfPageToCanvas,
  renderPdfThumbToCanvas,
  snapPdfZoom,
  PDF_ZOOM_PRESETS,
  PDF_DEFAULT_ZOOM,
} from "@/lib/pdf";
import {
  normalizeMcqOptions,
  sanitizeMcqStem,
  type ArtifactMeta,
  type AssertionPayload,
  type McqGradeResponse,
  type McqState,
  type PagesInfo,
} from "@/lib/types";

const SOURCE_PANEL_DEFAULT = 360;
const SOURCE_PANEL_MIN = 280;
const SOURCE_PANEL_MAX = 720;
const TUTOR_PANEL_DEFAULT = 400;
const TUTOR_PANEL_MIN = 300;
const TUTOR_PANEL_MAX = 560;
const STUDY_CENTER_MIN = 380;
const PANEL_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";
const PANEL_MS = 280;
// Desktop 3-pane only ≥992px (62em); below that the clean single-column mobile
// shell is used — the cramped 3-pane didn't fit small tablets / large phones.
const STUDY_DESKTOP_BP = "(min-width: 62em)";
const STUDY_COMPACT_BP = "(max-width: 47.99em)";
/** Tablet range (768–991px): desktop study shell, but Source/Tutor ride as floating slide-over panels. */
const STUDY_OVERLAY_BP = "(max-width: 61.99em)";
const THUMB_GAP = 28;
const THUMB_GAP_COMPACT = 14;
const THUMB_MIN_WIDTH = 248;
const THUMB_MIN_WIDTH_COMPACT = 148;
const THUMB_MAX_WIDTH = 320;
const THUMB_MAX_COLS = 4;
const THUMB_MAX_COLS_COMPACT = 2;
const SELECTION_PAD_X = 28;
const SELECTION_PAD_Y = 16;
const SELECTION_PAD_Y_COMPACT = 10;
const SELECTION_PAD_X_COMPACT = 16;
const SELECTION_DOCK_WIDTH = "80%";
const SELECTION_DOCK_RESERVE = 156;
const SELECTION_DOCK_RESERVE_COMPACT = 168;
const THUMB_FRAME_ASPECT = 1.414;
const THUMB_FRAME_ASPECT_COMPACT = 1.414;

function shellBleedPx(compact: boolean): number {
  return compact ? 0 : 16;
}

function computeGridLayout(
  gridWidth: number,
  minThumbWidth: number,
  gap: number,
  maxCols: number,
  maxThumbWidth = THUMB_MAX_WIDTH,
) {
  if (gridWidth < 1) return { cols: 2, thumbWidth: minThumbWidth };
  const cols = Math.min(
    maxCols,
    Math.max(1, Math.floor((gridWidth + gap) / (minThumbWidth + gap))),
  );
  const totalGap = gap * Math.max(0, cols - 1);
  const thumbWidth = Math.min(maxThumbWidth, Math.floor((gridWidth - totalGap) / cols));
  return { cols, thumbWidth };
}

/** A question the learner has already answered — kept client-side so they can step
 *  back and review any prior answer (with the choice they made + the explanation). */
type AnsweredCard = {
  assertionId: string;
  stem: string;
  options: string[];
  selectedIndex: number;
  gradeState: { correct: boolean; correctIndex: number };
  feedback: string | null;
};

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
  const useOverlayRails = useMediaQuery(STUDY_OVERLAY_BP, false, { getInitialValueInEffect: true });
  const mounted = useMounted();
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
  const [feedback, setFeedback] = useState<string | null>(null);
  const [gradeState, setGradeState] = useState<{ correct: boolean; correctIndex: number } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [questionSequence, setQuestionSequence] = useState<number | null>(null);
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
  const studyRowRef = useRef<HTMLDivElement>(null);
  const [studyRowWidth, setStudyRowWidth] = useState(0);
  const [sourcePanelWidth, setSourcePanelWidth] = useLocalStorage({
    key: "zivo-source-panel-width",
    defaultValue: SOURCE_PANEL_DEFAULT,
  });
  const [tutorPanelWidth, setTutorPanelWidth] = useLocalStorage({
    key: "zivo-tutor-panel-width",
    defaultValue: TUTOR_PANEL_DEFAULT,
  });
  const [resizingSource, setResizingSource] = useState(false);
  const [resizingTutor, setResizingTutor] = useState(false);
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
  const chatHydratedRef = useRef<string | null>(null);

  useEffect(() => {
    if (artifactQuery.data) setArtifact(artifactQuery.data);
    if (artifactQuery.error) {
      setSetupError(artifactQuery.error instanceof Error ? artifactQuery.error.message : "Could not load source");
    }
  }, [artifactQuery.data, artifactQuery.error]);

  useEffect(() => {
    if (pagesQuery.data) {
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
  }, [artifactId, invalidArtifactId, isPdf, artifact?.id]);

  useEffect(() => {
    if (!pdfDoc?.numPages || pdfDoc.numPages <= 1) return;
    const n = pdfDoc.numPages;
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

  const sourcePanelMax = useMemo(() => {
    if (studyRowWidth < 1) return SOURCE_PANEL_MAX;
    const other = tutorOpen ? tutorPanelWidth : 0;
    return clampPanel(
      studyRowWidth - STUDY_CENTER_MIN - other,
      SOURCE_PANEL_MIN,
      SOURCE_PANEL_MAX,
    );
  }, [studyRowWidth, tutorOpen, tutorPanelWidth]);

  const tutorPanelMax = useMemo(() => {
    if (studyRowWidth < 1) return TUTOR_PANEL_MAX;
    const other = sourceOpen ? sourcePanelWidth : 0;
    return clampPanel(
      studyRowWidth - STUDY_CENTER_MIN - other,
      TUTOR_PANEL_MIN,
      TUTOR_PANEL_MAX,
    );
  }, [studyRowWidth, sourceOpen, sourcePanelWidth]);

  useEffect(() => {
    const el = studyRowRef.current;
    if (!el) return;
    const update = () => setStudyRowWidth(el.clientWidth);
    update();
    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => ro.disconnect();
  }, [isLg, artifact?.id]);

  useEffect(() => {
    if (sourcePanelWidth > sourcePanelMax) {
      setSourcePanelWidth(sourcePanelMax);
    }
  }, [sourcePanelMax, sourcePanelWidth, setSourcePanelWidth]);

  useEffect(() => {
    if (tutorPanelWidth > tutorPanelMax) {
      setTutorPanelWidth(tutorPanelMax);
    }
  }, [tutorPanelMax, tutorPanelWidth, setTutorPanelWidth]);

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
    setOptions([]);
    setSelected(null);
    setGradeState(null);
    setFeedback(null);
  }, [invalidArtifactId, queue?.current_assertion_id]);

  useEffect(() => {
    if (invalidArtifactId || !queue?.current_assertion_id) return;
    if (assertionQuery.isError) {
      setQuestion("Could not load question.");
      setOptions([]);
      setQuestionSequence(null);
      return;
    }
    if (!assertionQuery.data) return;
    const row = assertionQuery.data;
    const p = (row.payload ?? {}) as AssertionPayload;
    setQuestion(sanitizeMcqStem(p.question ?? p.stem ?? row.title ?? "Question"));
    setOptions(normalizeMcqOptions(p.options, p.choices));
    setQuestionSequence(typeof p.sequence === "number" ? p.sequence : null);
    setSelected(null);
    setFeedback(null);
    setGradeState(null);
  }, [assertionQuery.data, assertionQuery.isError, queue?.current_assertion_id, invalidArtifactId]);

  useEffect(() => {
    setGradeState(null);
    setFeedback(null);
  }, [mode]);

  useEffect(() => {
    if (!queue?.page_complete || queue.current_assertion_id || queue.document_complete) return;
    const id = window.setTimeout(() => {
      void refreshQueue();
    }, 1500);
    return () => window.clearTimeout(id);
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
      setAnsweredHistory((h) => [
        ...h.filter((c) => c.assertionId !== answeredId),
        {
          assertionId: answeredId,
          stem,
          options: [...options],
          selectedIndex: answeredSelection,
          gradeState: { correct, correctIndex },
          feedback: gradedFeedback,
        },
      ]);
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
            {useOverlayRails && (sourceOpen || tutorOpen) && (
              <Box
                pos="absolute"
                inset={0}
                style={{ zIndex: 15, background: "rgba(0, 0, 0, 0.28)" }}
                onClick={() => {
                  if (sourceOpen) closeSource();
                  if (tutorOpen) closeTutor();
                }}
                aria-hidden
              />
            )}
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
            <StudyPushRail
              open={sourceOpen}
              width={clampPanel(sourcePanelWidth, SOURCE_PANEL_MIN, sourcePanelMax)}
              minWidth={SOURCE_PANEL_MIN}
              maxWidth={sourcePanelMax}
              side="left"
              title={shortFilename}
              onClose={closeSource}
              headerSize="compact"
              resizable
              overlay={Boolean(useOverlayRails)}
              isResizing={resizingSource}
              onResizeStart={() => setResizingSource(true)}
              onResizeEnd={() => setResizingSource(false)}
              onWidthChange={(next) =>
                setSourcePanelWidth(clampPanel(next, SOURCE_PANEL_MIN, sourcePanelMax))
              }
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
            </StudyPushRail>

            <Box flex={1} mih={0} pos="relative" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
              <Box
                flex={1}
                mih={0}
                pos="relative"
                style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}
              >
                {questionColumn}
              </Box>
            </Box>

            <StudyPushRail
              open={tutorOpen}
              width={clampPanel(tutorPanelWidth, TUTOR_PANEL_MIN, tutorPanelMax)}
              minWidth={TUTOR_PANEL_MIN}
              maxWidth={tutorPanelMax}
              side="right"
              title={ZIVO_ASSISTANT_NAME}
              onClose={closeTutor}
              headerSize="compact"
              resizable
              overlay={Boolean(useOverlayRails)}
              isResizing={resizingTutor}
              onResizeStart={() => setResizingTutor(true)}
              onResizeEnd={() => setResizingTutor(false)}
              onWidthChange={(next) =>
                setTutorPanelWidth(clampPanel(next, TUTOR_PANEL_MIN, tutorPanelMax))
              }
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
              />
            </StudyPushRail>
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

function suggestNextPageRange(
  completed: { from: number; to: number },
  pageCount: number,
): { from: number; to: number; bookFinished: boolean } {
  const span = Math.max(0, completed.to - completed.from);
  const nextFrom = completed.to + 1;
  if (nextFrom > pageCount) {
    return {
      from: completed.from,
      to: Math.min(completed.from + span, pageCount),
      bookFinished: true,
    };
  }
  return {
    from: nextFrom,
    to: Math.min(nextFrom + span, pageCount),
    bookFinished: false,
  };
}

/** Summative score screen shown at the end of a Test — the payoff Learn never shows. */
function TestResultsScreen({
  correct,
  total,
  compact,
  canChoosePages,
  onReview,
  onChoosePages,
}: {
  correct: number;
  total: number;
  compact?: boolean;
  canChoosePages?: boolean;
  onReview: () => void;
  onChoosePages: () => void;
}) {
  const pct = total > 0 ? Math.round((correct / total) * 100) : 0;
  const tone = pct >= 80 ? "sage" : pct >= 50 ? "forest" : "terracotta";
  const verdict = pct >= 80 ? "Excellent" : pct >= 50 ? "Solid work" : "Keep practicing";
  const RING = compact ? 150 : 184;
  const R = RING / 2 - 12;
  const C = 2 * Math.PI * R;
  const center = RING / 2;
  return (
    <Center h="100%" py={compact ? "md" : "lg"}>
      <Stack align="center" gap={compact ? "md" : "lg"} maw={440} px="md">
        <style>{`
          @keyframes zv-score-in { from { opacity: 0; transform: translateY(10px) scale(0.96); } to { opacity: 1; transform: none; } }
          @keyframes zv-ring-draw { from { stroke-dashoffset: ${C}; } }
          .zv-score { animation: zv-score-in 520ms cubic-bezier(0.32,0.72,0,1) both; }
          .zv-ring-fill { animation: zv-ring-draw 900ms cubic-bezier(0.32,0.72,0,1) 120ms both; }
          @media (prefers-reduced-motion: reduce) { .zv-score, .zv-ring-fill { animation: none !important; } }
        `}</style>
        <Text fz="xs" fw={700} tt="uppercase" c="dimmed" style={{ letterSpacing: "0.12em" }}>
          Test complete
        </Text>
        <Box className="zv-score" pos="relative" w={RING} h={RING} style={{ display: "grid", placeItems: "center" }}>
          <svg width={RING} height={RING} viewBox={`0 0 ${RING} ${RING}`}>
            <circle cx={center} cy={center} r={R} fill="none" stroke="var(--mantine-color-gray-3)" strokeWidth={10} />
            <circle
              className="zv-ring-fill"
              cx={center}
              cy={center}
              r={R}
              fill="none"
              stroke={`var(--mantine-color-${tone}-6)`}
              strokeWidth={10}
              strokeLinecap="round"
              strokeDasharray={C}
              strokeDashoffset={C * (1 - pct / 100)}
              transform={`rotate(-90 ${center} ${center})`}
            />
          </svg>
          <Stack pos="absolute" gap={0} align="center">
            <Text fz={compact ? 34 : 42} fw={600} c="var(--mantine-color-text)" style={{ fontFamily: "var(--font-serif), Georgia, serif", lineHeight: 1, letterSpacing: "-0.02em" }}>
              {pct}%
            </Text>
            <Text fz="sm" c="dimmed" fw={600} mt={4} style={{ fontVariantNumeric: "tabular-nums" }}>
              {correct} / {total} correct
            </Text>
          </Stack>
        </Box>
        <Stack gap={4} align="center">
          <Text ff="var(--font-serif)" fz={compact ? 22 : 26} fw={500} c="var(--mantine-color-text)" style={{ letterSpacing: "-0.01em" }}>
            {verdict}
          </Text>
          <Text c="dimmed" fz="sm" ta="center" maw={320} lh={1.55}>
            Review every question to see the correct answers and the reasoning behind them.
          </Text>
        </Stack>
        <Stack gap={8} w="100%" maw={300} mt="xs">
          <Button radius="xl" size="md" color="forest" onClick={onReview} leftSection={<IconHistory size={16} stroke={2} />}>
            Review answers
          </Button>
          {canChoosePages ? (
            <Button radius="xl" size="sm" variant="subtle" color="gray" onClick={onChoosePages}>
              Study new pages
            </Button>
          ) : null}
        </Stack>
      </Stack>
    </Center>
  );
}

function DocumentCompleteScreen({
  completedFrom,
  completedTo,
  pageCount,
  bookFinished,
  nextFrom,
  nextTo,
  compact,
  onChoosePages,
}: {
  completedFrom: number;
  completedTo: number;
  pageCount: number;
  bookFinished: boolean;
  nextFrom?: number;
  nextTo?: number;
  compact?: boolean;
  onChoosePages: () => void;
}) {
  const pageLabel =
    completedFrom === completedTo
      ? `Page ${completedFrom}`
      : `Pages ${completedFrom}–${completedTo}`;

  return (
    <Center py={compact ? "lg" : "xl"} px="md" h="100%">
      <Stack align="center" gap={compact ? "lg" : "xl"} maw={440}>
        <Stack align="center" gap="xs">
          <ThemeIcon size={52} radius="xl" variant="light" color="sage">
            <IconClipboardList size={26} stroke={1.5} />
          </ThemeIcon>
          <Title
            order={2}
            ta="center"
            fw={500}
            style={{ letterSpacing: "-0.01em", lineHeight: 1.2, fontFamily: "var(--font-serif), Georgia, serif" }}
          >
            {pageLabel} complete
          </Title>
          <Text size="sm" c="dimmed" ta="center" lh={1.6} maw={360}>
            {bookFinished
              ? `You've worked through every page in this ${pageCount}-page book. Pick any range to study again, or continue elsewhere in your library.`
              : "Every question in this range is done. When you're ready, choose the next pages from the same source."}
          </Text>
        </Stack>

        {!bookFinished && nextFrom !== undefined && nextTo !== undefined && (
          <Paper withBorder radius="lg" p="md" w="100%" bg="var(--mantine-color-body)">
            <Text size="xs" tt="uppercase" fw={600} c="dimmed" mb={6}>
              Suggested next
            </Text>
            <Text size="lg" fw={600} style={{ letterSpacing: "-0.02em" }}>
              Pages {nextFrom}–{nextTo}
            </Text>
          </Paper>
        )}

        <Stack gap="sm" w="100%" maw={320}>
          <Button size="md" radius="xl" fullWidth onClick={onChoosePages}>
            {bookFinished ? "Choose pages to study" : "Choose next pages"}
          </Button>
        </Stack>
      </Stack>
    </Center>
  );
}

function StudyRangeReselectOverlay({
  filename,
  pageCount,
  completedRange,
  sliderFrom,
  sliderTo,
  sliderMarks,
  selectedPages,
  isDark,
  isCompact,
  isPdf,
  pdfDoc,
  thumbCanvasRefs,
  confirming,
  setupError,
  bookFinished,
  onRangeChange,
  onPageToggle,
  onSelectAll,
  onClearAll,
  onClose,
  onConfirm,
}: {
  filename: string;
  pageCount: number;
  completedRange?: { from: number; to: number };
  sliderFrom: number;
  sliderTo: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  selectedPages: number[];
  isDark: boolean;
  isCompact: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  confirming: boolean;
  setupError: string | null;
  bookFinished: boolean;
  onRangeChange: (from: number, to: number) => void;
  onPageToggle: (page: number, shiftKey: boolean) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const completedLabel =
    completedRange && completedRange.from === completedRange.to
      ? `page ${completedRange.from}`
      : completedRange
        ? `pages ${completedRange.from}–${completedRange.to}`
        : "your last selection";

  return (
    <Box
      pos="fixed"
      inset={0}
      style={{
        zIndex: 300,
        display: "flex",
        alignItems: isCompact ? "flex-end" : "center",
        justifyContent: "center",
        padding: isCompact ? 0 : "var(--mantine-spacing-md)",
        background: "rgba(0, 0, 0, 0.45)",
        backdropFilter: "blur(6px)",
      }}
      onClick={onClose}
    >
      <Paper
        shadow="xl"
        radius={isCompact ? 0 : "lg"}
        p={isCompact ? "md" : "xl"}
        w="100%"
        maw={isCompact ? "100%" : 900}
        mih={isCompact ? "85vh" : "min(88vh, 760px)"}
        onClick={(e) => e.stopPropagation()}
        style={{
          display: "flex",
          flexDirection: "column",
          borderBottomLeftRadius: isCompact ? 0 : undefined,
          borderBottomRightRadius: isCompact ? 0 : undefined,
          paddingBottom: isCompact ? "max(20px, env(safe-area-inset-bottom))" : undefined,
        }}
      >
        <Group justify="space-between" align="flex-start" mb="md" wrap="nowrap">
          <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
            <Text size="xs" tt="uppercase" fw={600} c="dimmed">
              {filename}
            </Text>
            <Title order={4} style={{ letterSpacing: "-0.03em" }}>
              Pick your next pages
            </Title>
            <Text size="sm" c="dimmed" lh={1.5}>
              {bookFinished
                ? `You finished ${completedLabel}. Tap any pages in this ${pageCount}-page book.`
                : `You finished ${completedLabel}. Choose the pages you want to study next.`}
            </Text>
          </Stack>
          <ActionIcon variant="subtle" color="gray" onClick={onClose} aria-label="Close">
            <IconX size={18} />
          </ActionIcon>
        </Group>
        <Box flex={1} mih={0} style={{ display: "flex", flexDirection: "column" }}>
          <PageSelectionBody
            padX={isCompact ? SELECTION_PAD_X_COMPACT : SELECTION_PAD_X}
            pageCount={pageCount}
            sliderFrom={sliderFrom}
            sliderTo={sliderTo}
            sliderMarks={sliderMarks}
            selectedPages={selectedPages}
            isDark={isDark}
            isPdf={isPdf}
            pdfDoc={pdfDoc}
            thumbCanvasRefs={thumbCanvasRefs}
            confirming={confirming}
            setupError={setupError}
            confirmLabel="Start studying"
            onRangeChange={onRangeChange}
            onPageToggle={onPageToggle}
            onSelectAll={onSelectAll}
            onClearAll={onClearAll}
            onConfirm={onConfirm}
          />
        </Box>
      </Paper>
    </Box>
  );
}

function PageCompleteInterstitial({
  compact,
  generating,
}: {
  page: number;
  compact?: boolean;
  generating?: boolean;
}) {
  return (
    <Center py={compact ? "md" : "xl"}>
      <Stack align="center" gap="md" maw={320}>
        <Loader type="oval" size="sm" />
        <Text size="lg" fw={500} ta="center" style={{ letterSpacing: "-0.02em" }}>
          Well done
        </Text>
        <Text size="sm" c="dimmed" ta="center" lh={1.55}>
          {generating ? "Preparing what's next…" : "Continuing…"}
        </Text>
      </Stack>
    </Center>
  );
}

function StudyMetaBar({
  questionIndex,
  questionTotal,
  mode,
  onModeChange,
  showProgress = true,
  compact = false,
  studyMode,
  onStudyModeChange,
}: {
  questionIndex: number;
  questionTotal: number;
  mode: StudyMode;
  onModeChange: (mode: StudyMode) => void;
  showProgress?: boolean;
  compact?: boolean;
  studyMode?: "adaptive" | "classic";
  onStudyModeChange?: (mode: "adaptive" | "classic") => void;
}) {
  const showBar = showProgress && questionTotal > 0;
  const pct = showBar ? Math.min(100, Math.round((questionIndex / questionTotal) * 100)) : 0;
  const segmented = showBar && questionTotal <= 16;
  const isTestMode = mode === "test";
  // Test wears the brand's deep green; Learn keeps lavender — a constant, glanceable
  // signal that the two are different study contexts.
  const barAccent = isTestMode ? "forest" : "lavender";
  const modeBadge =
    mode === "learn" || mode === "test" ? (
      <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
        <ThemeIcon size={20} radius="xl" variant="light" color={barAccent}>
          {isTestMode ? <IconClipboardList size={12} stroke={2} /> : <IconBulb size={12} stroke={2} />}
        </ThemeIcon>
        <Text fz="xs" fw={700} tt="uppercase" c={`var(--mantine-color-${barAccent}-${isTestMode ? 8 : 7})`} style={{ letterSpacing: "0.04em" }}>
          {isTestMode ? "Test" : "Learn"}
        </Text>
      </Group>
    ) : null;

  // Adaptive vs Classic, switchable per document. Adaptive picks each next
  // question at the learner's edge; Classic walks a fixed set in order.
  const currentStudyMode = studyMode ?? "adaptive";
  const studyModeControl =
    (mode === "learn" || mode === "test") && onStudyModeChange ? (
      <Menu shadow="md" width={244} position="bottom-end" radius="md" withinPortal>
        <Menu.Target>
          <UnstyledButton
            aria-label="Change how questions are chosen"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              flexShrink: 0,
              padding: "5px 10px",
              borderRadius: 999,
              border: "1px solid var(--mantine-color-default-border)",
              background: "var(--mantine-color-body)",
            }}
          >
            <IconAdjustmentsHorizontal size={14} stroke={1.8} style={{ color: "var(--mantine-color-dimmed)" }} />
            <Text fz="xs" fw={600} c="var(--mantine-color-text)">
              {currentStudyMode === "classic" ? "Classic" : "Adaptive"}
            </Text>
            <IconChevronDown size={12} stroke={2} style={{ color: "var(--mantine-color-dimmed)" }} />
          </UnstyledButton>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Label>How questions are chosen</Menu.Label>
          <Menu.Item
            onClick={() => onStudyModeChange("adaptive")}
            rightSection={currentStudyMode !== "classic" ? <IconCheck size={15} stroke={2.4} color="var(--mantine-color-lavender-6)" /> : null}
          >
            <Text fz="sm" fw={500}>Adaptive tutor</Text>
            <Text fz="xs" c="dimmed">Questions adjust to your answers</Text>
          </Menu.Item>
          <Menu.Item
            onClick={() => onStudyModeChange("classic")}
            rightSection={currentStudyMode === "classic" ? <IconCheck size={15} stroke={2.4} color="var(--mantine-color-lavender-6)" /> : null}
          >
            <Text fz="sm" fw={500}>Classic</Text>
            <Text fz="xs" c="dimmed">A fixed set, in order</Text>
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    ) : null;

  const progress = !showBar ? null : (
    <Group gap={compact ? 8 : 12} wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
      <Text size="xs" c="dimmed" fw={600} ff="monospace" style={{ flexShrink: 0, letterSpacing: "0.02em" }}>
        {String(questionIndex).padStart(2, "0")}
        <Text component="span" inherit style={{ opacity: 0.45 }}>
          {" / "}
          {String(questionTotal).padStart(2, "0")}
        </Text>
      </Text>
      {segmented ? (
        <Group gap={4} wrap="nowrap" style={{ flex: 1, minWidth: 0, maxWidth: 380 }}>
          {Array.from({ length: questionTotal }).map((_, i) => (
            <Box
              key={i}
              style={{
                flex: 1,
                height: 5,
                borderRadius: 99,
                background: i < questionIndex ? `var(--mantine-color-${barAccent}-6)` : "var(--mantine-color-gray-3)",
                transition: "background 260ms ease",
              }}
            />
          ))}
        </Group>
      ) : (
        <Box style={{ flex: 1, maxWidth: 380, height: 5, borderRadius: 99, background: "var(--mantine-color-gray-3)", overflow: "hidden" }}>
          <Box style={{ width: `${pct}%`, height: "100%", borderRadius: 99, background: `var(--mantine-color-${barAccent}-6)`, transition: "width 320ms cubic-bezier(0.32,0.72,0,1)" }} />
        </Box>
      )}
    </Group>
  );

  // The 7-mode switch can't fit a phone row, so on compact it gets its own
  // full-width, horizontally-scrollable row beneath the progress.
  const modeSwitch = (
    <Box
      className="zv-modeswitch-scroll"
      style={{ minWidth: 0, maxWidth: "100%", overflowX: "auto", overflowY: "hidden", scrollbarWidth: "none" }}
    >
      <StudyModeSwitch mode={mode} onChange={onModeChange} compact={compact} />
    </Box>
  );

  if (compact) {
    // Phones can't fit a left mode rail, so the switch lives here above the progress.
    return (
      <Stack px="sm" py={6} gap={6} style={{ flexShrink: 0 }}>
        <style>{`.zv-modeswitch-scroll::-webkit-scrollbar { display: none; }`}</style>
        {modeSwitch}
        {progress || studyModeControl ? (
          <Group justify="space-between" wrap="nowrap" align="center" gap="sm">
            <Box style={{ flex: 1, minWidth: 0 }}>{progress}</Box>
            {studyModeControl}
          </Group>
        ) : null}
      </Stack>
    );
  }

  // Desktop/tablet: the sidebar owns mode switching, so the top bar carries the
  // mode identity + question progress + the Adaptive/Classic chooser — and nothing
  // at all in modes that have none (e.g. Read), so the content starts cleanly.
  if (!progress && !modeBadge && !studyModeControl) return null;
  return (
    <Group
      px={{ base: "sm", sm: "md", lg: "lg" }}
      py={8}
      justify="space-between"
      align="center"
      wrap="nowrap"
      gap="md"
      style={{ flexShrink: 0 }}
    >
      <Group gap="md" wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
        {modeBadge}
        {progress}
      </Group>
      {studyModeControl}
    </Group>
  );
}

function StudyModeSwitch({
  mode,
  onChange,
  compact = false,
}: {
  mode: StudyMode;
  onChange: (mode: StudyMode) => void;
  compact?: boolean;
}) {
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  type Mode = StudyMode;

  // Two intuitive groups instead of one crowded row: work directly with the material
  // (read / learn / test) vs. the AI study aids it generates.
  const core = ["read", "learn", "test"];
  const tools = ["explain", "notes", "cards", "palace", "quiz"];
  const styles = {
    root: {
      background: isDark ? "var(--mantine-color-dark-6)" : "var(--mantine-color-gray-1)",
      border: `1px solid ${isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)"}`,
    },
    label: {
      fontWeight: 600,
      paddingInline: compact ? 10 : 13,
      fontSize: compact ? 11 : 12,
      letterSpacing: "-0.01em",
    },
    indicator: { boxShadow: "none" },
  };

  return (
    <Group gap={compact ? 6 : 8} wrap="nowrap">
      <SegmentedControl
        size="xs"
        radius="xl"
        value={core.includes(mode) ? mode : ""}
        onChange={(v) => v && onChange(v as Mode)}
        data={[
          { label: "Read", value: "read" },
          { label: "Learn", value: "learn" },
          { label: "Test", value: "test" },
        ]}
        styles={styles}
      />
      <SegmentedControl
        size="xs"
        radius="xl"
        value={tools.includes(mode) ? mode : ""}
        onChange={(v) => v && onChange(v as Mode)}
        data={[
          { label: "Explain", value: "explain" },
          { label: "Notes", value: "notes" },
          { label: "Cards", value: "cards" },
          { label: "Palace", value: "palace" },
          { label: "Quiz", value: "quiz" },
        ]}
        styles={styles}
      />
    </Group>
  );
}

type StudyMobileTab = "question" | "source" | "tutor";

function StudyMobileShell({
  question,
  renderSource,
  renderTutor,
}: {
  question: ReactNode;
  renderSource: (visible: boolean) => ReactNode;
  renderTutor: () => ReactNode;
}) {
  const [active, setActive] = useState<StudyMobileTab>("question");

  const tabs: { id: StudyMobileTab; label: string; icon: typeof IconClipboardList }[] = [
    { id: "question", label: "Question", icon: IconClipboardList },
    { id: "source", label: "Source", icon: IconFileText },
    { id: "tutor", label: ZIVO_ASSISTANT_NAME, icon: IconMessageCircle },
  ];

  return (
    <Stack gap={0} flex={1} mih={0} style={{ overflow: "hidden" }}>
      <Box flex={1} mih={0} pos="relative" style={{ overflow: "hidden" }}>
        <StudyMobilePanel visible={active === "question"}>{question}</StudyMobilePanel>
        <StudyMobilePanel visible={active === "source"}>{renderSource(active === "source")}</StudyMobilePanel>
        <StudyMobilePanel visible={active === "tutor"}>{renderTutor()}</StudyMobilePanel>
      </Box>
      <Box
        component="nav"
        aria-label="Study sections"
        style={{
          flexShrink: 0,
          borderTop: "1px solid var(--mantine-color-default-border)",
          background: "var(--mantine-color-body)",
          paddingBottom: "max(6px, env(safe-area-inset-bottom))",
        }}
      >
        <Group grow gap={0}>
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const selected = active === tab.id;
            return (
              <UnstyledButton
                key={tab.id}
                onClick={() => setActive(tab.id)}
                aria-current={selected ? "page" : undefined}
                style={{
                  minHeight: 52,
                  padding: "6px 4px",
                  borderRadius: 0,
                  background: selected ? "var(--mantine-color-lavender-1)" : "transparent",
                }}
              >
                <Stack gap={2} align="center">
                  <Icon
                    size={22}
                    stroke={selected ? 2.25 : 1.75}
                    color={selected ? "var(--mantine-color-lavender-7)" : "var(--mantine-color-dimmed)"}
                  />
                  <Text size="10px" fw={selected ? 700 : 500} c={selected ? "lavender.7" : "dimmed"} lh={1.1}>
                    {tab.label}
                  </Text>
                </Stack>
              </UnstyledButton>
            );
          })}
        </Group>
      </Box>
    </Stack>
  );
}

function StudyMobilePanel({ visible, children }: { visible: boolean; children: ReactNode }) {
  return (
    <Box
      pos="absolute"
      inset={0}
      style={{
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        visibility: visible ? "visible" : "hidden",
        pointerEvents: visible ? "auto" : "none",
        zIndex: visible ? 1 : 0,
      }}
    >
      {children}
    </Box>
  );
}

function StudyEdgeTrigger({
  side,
  icon,
  label,
  color,
  onClick,
}: {
  side: "left" | "right";
  icon: ReactNode;
  label: string;
  color: string;
  onClick: () => void;
}) {
  return (
    <Tooltip label={`Open ${label}`} position={side === "left" ? "right" : "left"} withArrow openDelay={350}>
      <UnstyledButton
        onClick={onClick}
        aria-label={`Open ${label}`}
        className={`zivo-edge zivo-edge-${side}`}
        style={{ position: "absolute", top: "50%", [side]: 10, zIndex: 6 }}
      >
        <style>{`
          .zivo-edge {
            transform: translateY(-50%);
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 7px;
            padding: 10px 9px;
            border-radius: 16px;
            background: var(--mantine-color-gray-0);
            border: 1px solid var(--mantine-color-default-border);
            box-shadow: 0 8px 24px rgba(35, 34, 32, 0.10), 0 1px 2px rgba(35, 34, 32, 0.04);
            transition: transform 220ms cubic-bezier(0.32,0.72,0,1), box-shadow 220ms ease, border-color 220ms ease;
          }
          .zivo-edge:hover { box-shadow: 0 12px 32px rgba(35, 34, 32, 0.16), 0 2px 4px rgba(35, 34, 32, 0.06); }
          .zivo-edge-left:hover { transform: translateY(-50%) translateX(4px); }
          .zivo-edge-right:hover { transform: translateY(-50%) translateX(-4px); }
          .zivo-edge-chip {
            width: 32px; height: 32px; border-radius: 10px;
            display: flex; align-items: center; justify-content: center;
          }
          .zivo-edge-label { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; line-height: 1; color: var(--mantine-color-dimmed); }
          @media (prefers-reduced-motion: reduce) { .zivo-edge { transition: none; } }
        `}</style>
        <span
          className="zivo-edge-chip"
          style={{ background: `var(--mantine-color-${color}-0)`, color: `var(--mantine-color-${color}-7)` }}
        >
          {icon}
        </span>
        <span className="zivo-edge-label">{label}</span>
      </UnstyledButton>
    </Tooltip>
  );
}

function StudyPushRail({
  open,
  width,
  minWidth,
  maxWidth,
  side,
  title,
  onClose,
  children,
  headerSize = "default",
  resizable = false,
  overlay = false,
  isResizing = false,
  onResizeStart,
  onResizeEnd,
  onWidthChange,
}: {
  open: boolean;
  width: number;
  minWidth?: number;
  maxWidth?: number;
  side: "left" | "right";
  title: string;
  onClose: () => void;
  children: ReactNode;
  headerSize?: "default" | "compact";
  resizable?: boolean;
  overlay?: boolean;
  isResizing?: boolean;
  onResizeStart?: () => void;
  onResizeEnd?: () => void;
  onWidthChange?: (width: number) => void;
}) {
  const railBorder = "1px solid var(--mantine-color-default-border)";
  // Tablet slide-over: cap the panel at ~58% of its row so the question column
  // underneath is never cramped, regardless of the stored drag width.
  const overlayWidth = Math.min(width, 460);

  return (
    <Box
      pos={overlay ? "absolute" : "relative"}
      h="100%"
      style={
        overlay
          ? {
              top: 0,
              bottom: 0,
              [side]: 0,
              width: open ? overlayWidth : 0,
              maxWidth: "82%",
              flexShrink: 0,
              overflow: "hidden",
              zIndex: open ? 20 : 1,
              transition: isResizing
                ? undefined
                : `width ${PANEL_MS}ms ${PANEL_EASE}, transform ${PANEL_MS}ms ${PANEL_EASE}`,
              transform: open
                ? "translateX(0)"
                : side === "left"
                  ? "translateX(-100%)"
                  : "translateX(100%)",
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
              boxShadow: open ? "0 12px 40px rgba(0, 0, 0, 0.35)" : undefined,
              borderRight: open && side === "left" ? railBorder : undefined,
              borderLeft: open && side === "right" ? railBorder : undefined,
              borderRadius: side === "left" ? "0 16px 16px 0" : "16px 0 0 16px",
            }
          : {
              width: open ? width : 0,
              flexShrink: 0,
              alignSelf: "stretch",
              overflow: "hidden",
              transition: isResizing ? undefined : `width ${PANEL_MS}ms ${PANEL_EASE}`,
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
              borderRight: open && side === "left" ? railBorder : undefined,
              borderLeft: open && side === "right" ? railBorder : undefined,
            }
      }
      >
      <Box
        w={overlay ? overlayWidth : width}
        h="100%"
        mih={0}
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          opacity: open ? 1 : 0,
          transform: open ? "translateX(0)" : side === "left" ? "translateX(-12px)" : "translateX(12px)",
          transition: isResizing
            ? undefined
            : `opacity ${PANEL_MS}ms ${PANEL_EASE} ${open ? 50 : 0}ms, transform ${PANEL_MS}ms ${PANEL_EASE} ${open ? 50 : 0}ms`,
          pointerEvents: open ? "auto" : "none",
        }}
      >
        <Group
          px="md"
          py={8}
          justify="space-between"
          wrap="nowrap"
          gap="xs"
          style={{
            flexShrink: 0,
            borderBottom: open ? railBorder : undefined,
            minHeight: 44,
            background: "var(--mantine-color-body)",
          }}
        >
          <Text size="sm" fw={600} truncate c="var(--mantine-color-text)" style={{ letterSpacing: "-0.01em" }}>
            {title}
          </Text>
          <ActionIcon variant="subtle" color="gray" radius="xl" size="md" onClick={onClose} aria-label={`Close ${title}`} style={{ flexShrink: 0 }}>
            <IconX size={17} stroke={1.8} />
          </ActionIcon>
        </Group>
        <Box flex={1} mih={0} style={{ display: "flex", flexDirection: "column" }}>
          {children}
        </Box>
      </Box>
      {open && resizable && !overlay && onWidthChange && (
        <PanelResizeHandle
          side={side}
          minWidth={minWidth ?? 280}
          maxWidth={maxWidth ?? 720}
          width={width}
          onResizeStart={onResizeStart}
          onResizeEnd={onResizeEnd}
          onWidthChange={onWidthChange}
        />
      )}
    </Box>
  );
}

function PanelResizeHandle({
  side,
  width,
  minWidth,
  maxWidth,
  onWidthChange,
  onResizeStart,
  onResizeEnd,
}: {
  side: "left" | "right";
  width: number;
  minWidth: number;
  maxWidth: number;
  onWidthChange: (width: number) => void;
  onResizeStart?: () => void;
  onResizeEnd?: () => void;
}) {
  const dragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);

  function endDrag(target: EventTarget & Element, pointerId: number) {
    dragging.current = false;
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    if (target.hasPointerCapture(pointerId)) {
      target.releasePointerCapture(pointerId);
    }
    onResizeEnd?.();
  }

  return (
    <Tooltip label="Drag to resize" position={side === "left" ? "right" : "left"} withArrow openDelay={500}>
      <Box
        role="separator"
        aria-orientation="vertical"
        aria-valuenow={width}
        aria-valuemin={minWidth}
        aria-valuemax={maxWidth}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          dragging.current = true;
          startX.current = e.clientX;
          startWidth.current = width;
          onResizeStart?.();
          document.body.style.cursor = "col-resize";
          document.body.style.userSelect = "none";
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!dragging.current) return;
          const delta = e.clientX - startX.current;
          const next =
            side === "left" ? startWidth.current + delta : startWidth.current - delta;
          onWidthChange(clampPanel(next, minWidth, maxWidth));
        }}
        onPointerUp={(e) => endDrag(e.currentTarget, e.pointerId)}
        onPointerCancel={(e) => endDrag(e.currentTarget, e.pointerId)}
        style={{
          position: "absolute",
          top: 0,
          bottom: 0,
          [side === "left" ? "right" : "left"]: 0,
          transform: side === "left" ? "translateX(50%)" : "translateX(-50%)",
          width: 10,
          cursor: "col-resize",
          zIndex: 30,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          touchAction: "none",
        }}
      >
        <Box
          w={4}
          h={48}
          style={{
            borderRadius: 999,
            background: "var(--mantine-color-default-border)",
            opacity: 0.9,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <IconGripVertical size={12} stroke={1.5} color="var(--mantine-color-dimmed)" />
        </Box>
      </Box>
    </Tooltip>
  );
}

function StudySourcePanel({
  filename,
  pageRange,
  currentPage,
  isPdf,
  pdfLoading,
  pdfError,
  pdfDoc,
  studyPages,
  open,
}: {
  filename?: string;
  pageRange?: { from: number; to: number };
  currentPage?: number;
  isPdf: boolean;
  pdfLoading: boolean;
  pdfError: string | null;
  pdfDoc: PDFDocumentProxy | null;
  studyPages: number[];
  open: boolean;
}) {
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  const [zoom, setZoom] = useState(PDF_DEFAULT_ZOOM);
  const [viewerWidth, setViewerWidth] = useState(0);
  const [pageAspects, setPageAspects] = useState<Record<number, number>>({});
  const [panning, setPanning] = useState(false);
  const canvasRefs = useRef<Record<number, HTMLCanvasElement | null>>({});
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const viewportRef = useRef<HTMLDivElement>(null);
  const panState = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    scrollLeft: number;
    scrollTop: number;
  } | null>(null);

  const displayPages = useMemo(() => {
    if (currentPage && currentPage >= 1) {
      return [currentPage];
    }
    return studyPages;
  }, [currentPage, studyPages]);

  const zoomMin = PDF_ZOOM_PRESETS[0];
  const zoomMax = PDF_ZOOM_PRESETS[PDF_ZOOM_PRESETS.length - 1];
  const canPan = zoom > 1.01;

  useEffect(() => {
    if (!open) {
      setZoom(PDF_DEFAULT_ZOOM);
      setViewerWidth(0);
    }
  }, [open]);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el || !open) return;
    const update = () => {
      const w = el.clientWidth;
      if (w > 0) setViewerWidth(w);
    };
    update();
    const raf = requestAnimationFrame(update);
    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [open]);

  useEffect(() => {
    if (!pdfDoc || !open) return;
    let cancelled = false;
    void (async () => {
      const aspects: Record<number, number> = {};
      for (const p of displayPages) {
        if (cancelled) return;
        aspects[p] = await pdfPageAspectRatio(pdfDoc, p);
      }
      if (!cancelled) setPageAspects(aspects);
    })();
    return () => {
      cancelled = true;
    };
  }, [pdfDoc, displayPages, open]);

  const pageDisplayWidth = viewerWidth > 0 ? Math.round(viewerWidth * zoom) : 0;

  useEffect(() => {
    if (!pdfDoc || !isPdf || !open || pageDisplayWidth < 1) return;
    let cancelled = false;
    // Snapshot the (stable) canvas registry so the cleanup cancels exactly the
    // renders this effect kicked off, without reading the ref at teardown time.
    const canvases = canvasRefs.current;

    void (async () => {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
      if (cancelled) return;
      for (const p of displayPages) {
        if (cancelled) return;
        const canvas = canvases[p];
        if (!canvas) continue;
        try {
          const fitScale = await pdfPageFitScale(pdfDoc, p, viewerWidth);
          if (cancelled) return;
          await renderPdfPageToCanvas(pdfDoc, p, canvas, fitScale * zoom, pageDisplayWidth);
        } catch {
          if (cancelled) return;
        }
      }
      if (!cancelled) {
        const el = viewportRef.current;
        if (el) clampPdfScroll(el);
      }
    })();

    return () => {
      cancelled = true;
      cancelAllPdfRenders(Object.values(canvases));
    };
  }, [pdfDoc, displayPages, isPdf, open, viewerWidth, zoom, pageDisplayWidth]);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    clampPdfScroll(el);
  }, [pageDisplayWidth, displayPages, pageAspects]);

  useEffect(() => {
    if (!open) return;
    const el = viewportRef.current;
    if (!el) return;
    el.scrollLeft = 0;
    el.scrollTop = 0;
  }, [open, currentPage, displayPages]);

  function zoomIn() {
    setZoom((z) => snapPdfZoom(z, 1));
  }

  function zoomOut() {
    setZoom((z) => snapPdfZoom(z, -1));
  }

  function fitWidth() {
    setZoom(1);
    const el = viewportRef.current;
    if (el) {
      el.scrollLeft = 0;
      el.scrollTop = 0;
    }
  }

  function onWheelZoom(e: WheelEvent<HTMLDivElement>) {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    setZoom((z) => snapPdfZoom(z, e.deltaY > 0 ? -1 : 1));
  }

  function onViewportScroll() {
    const el = viewportRef.current;
    if (!el || panState.current) return;
    clampPdfScroll(el);
  }

  function onPanPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    if (!canPan || e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (!target.closest("[data-pdf-page]")) return;
    const el = viewportRef.current;
    if (!el) return;
    e.preventDefault();
    panState.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      scrollLeft: el.scrollLeft,
      scrollTop: el.scrollTop,
    };
    setPanning(true);
    el.setPointerCapture(e.pointerId);
  }

  function onPanPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    const pan = panState.current;
    const el = viewportRef.current;
    if (!pan || pan.pointerId !== e.pointerId || !el) return;
    const dx = e.clientX - pan.startX;
    const dy = e.clientY - pan.startY;
    el.scrollLeft = pan.scrollLeft - dx;
    el.scrollTop = pan.scrollTop - dy;
    clampPdfScroll(el);
  }

  function endPan(e: ReactPointerEvent<HTMLDivElement>) {
    const pan = panState.current;
    if (!pan || pan.pointerId !== e.pointerId) return;
    panState.current = null;
    setPanning(false);
    const el = viewportRef.current;
    if (el?.hasPointerCapture(e.pointerId)) {
      el.releasePointerCapture(e.pointerId);
    }
    if (el) clampPdfScroll(el);
  }

  if (!isPdf) {
    return (
      <ScrollArea flex={1} offsetScrollbars type="auto">
        <SourceStage filename={filename} pageRange={pageRange} compact inDrawer />
      </ScrollArea>
    );
  }

  if (pdfLoading) {
    return (
      <Center flex={1}>
        <Stack align="center" gap="sm">
          <Loader size="sm" color="lavender" />
          <Text size="sm" c="dimmed">
            Loading document…
          </Text>
        </Stack>
      </Center>
    );
  }

  if (pdfError) {
    return (
      <Center flex={1} px="lg">
        <Stack align="center" gap="sm" maw={300}>
          <ThemeIcon size={44} radius="xl" variant="light" color="terracotta">
            <IconFileText size={22} stroke={1.6} />
          </ThemeIcon>
          <Text c="terracotta.8" size="sm" ta="center" lh={1.55}>
            {pdfError}
          </Text>
        </Stack>
      </Center>
    );
  }

  const pageSurface = isDark ? "white" : "white";
  const pageGap = displayPages.length > 1 ? 8 : 0;
  const activePage = displayPages[0];

  return (
    <Box pos="relative" h="100%" mih={0} style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <Box
        ref={viewportRef}
        flex={1}
        mih={0}
        onWheel={onWheelZoom}
        onScroll={onViewportScroll}
        onPointerDown={onPanPointerDown}
        onPointerMove={onPanPointerMove}
        onPointerUp={endPan}
        onPointerCancel={endPan}
        style={{
          overflow: "auto",
          overscrollBehavior: "contain",
          background: isDark ? "var(--mantine-color-dark-8)" : "var(--mantine-color-gray-1)",
          cursor: canPan ? (panning ? "grabbing" : "grab") : "default",
          touchAction: panning ? "none" : "auto",
        }}
      >
        <Box
          style={{
            width: "max-content",
            minWidth: "100%",
            margin: "0 auto",
            padding: "18px 16px 68px",
          }}
        >
          <Stack gap={pageGap > 0 ? 14 : 0} align="center">
            {pdfDoc &&
              displayPages.map((p) => {
                const aspect = pageAspects[p] ?? 0;
                const displayHeight =
                  pageDisplayWidth > 0 && aspect > 0 ? pdfDisplayHeight(pageDisplayWidth, aspect) : undefined;
                const isActivePage = p === activePage;
                return (
                  <Box
                    key={p}
                    data-pdf-page
                    ref={(el) => {
                      pageRefs.current[p] = el;
                    }}
                    pos="relative"
                    style={{
                      width: pageDisplayWidth > 0 ? pageDisplayWidth : "100%",
                      maxWidth: "100%",
                      lineHeight: 0,
                      borderRadius: 10,
                      background: pageSurface,
                      border: "1px solid var(--mantine-color-default-border)",
                      boxShadow: isActivePage
                        ? "0 0 0 2px var(--mantine-color-lavender-5), 0 14px 40px rgba(35,34,32,0.14)"
                        : "0 2px 6px rgba(35,34,32,0.06), 0 14px 30px rgba(35,34,32,0.06)",
                      overflow: "hidden",
                      transition: "box-shadow 220ms ease",
                    }}
                  >
                    <canvas
                      ref={(el) => {
                        canvasRefs.current[p] = el;
                      }}
                      style={{
                        width: pageDisplayWidth > 0 ? pageDisplayWidth : "100%",
                        height: displayHeight ? `${displayHeight}px` : "auto",
                        display: "block",
                        maxWidth: "none",
                        verticalAlign: "top",
                      }}
                    />
                    {displayPages.length > 1 && (
                      <Box
                        style={{
                          position: "absolute",
                          top: 8,
                          left: 8,
                          padding: "3px 9px",
                          borderRadius: 999,
                          fontSize: 10,
                          fontWeight: 700,
                          lineHeight: 1,
                          fontFamily: "var(--font-sans), sans-serif",
                          color: "#FFFFFF",
                          // This badge overlays the PDF canvas, which is always
                          // white in BOTH color schemes (pageSurface = "white").
                          // The lavender scale is inverted for dark mode (high
                          // shades go pale for text-on-ink), so no single
                          // lavender token stays dark in both schemes. Pin a
                          // fixed deep lavender so white label text stays legible
                          // on the invariant white page.
                          background: isActivePage ? "#644791" : "rgba(35,34,32,0.55)",
                          backdropFilter: "blur(4px)",
                          WebkitBackdropFilter: "blur(4px)",
                        }}
                      >
                        Page {p}
                      </Box>
                    )}
                  </Box>
                );
              })}
          </Stack>
        </Box>
      </Box>

      <PdfReaderToolbar
        zoom={zoom}
        zoomMin={zoomMin}
        zoomMax={zoomMax}
        onZoomIn={zoomIn}
        onZoomOut={zoomOut}
        onFitWidth={fitWidth}
        isDark={isDark}
        canPan={canPan}
      />
    </Box>
  );
}

function PdfReaderToolbar({
  zoom,
  zoomMin,
  zoomMax,
  onZoomIn,
  onZoomOut,
  onFitWidth,
}: {
  zoom: number;
  zoomMin: number;
  zoomMax: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitWidth: () => void;
  isDark?: boolean;
  canPan?: boolean;
}) {
  const atFit = Math.abs(zoom - 1) < 0.01;

  return (
    <Box
      style={{
        position: "absolute",
        bottom: 14,
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: 6,
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "6px 8px",
        borderRadius: 999,
        background: "color-mix(in srgb, var(--mantine-color-body) 86%, transparent)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        border: "1px solid var(--mantine-color-default-border)",
        boxShadow: "0 6px 24px rgba(35,34,32,0.16)",
      }}
    >
      <Tooltip label="Fit page width" withArrow>
        <Button
          variant={atFit ? "light" : "subtle"}
          color={atFit ? "lavender" : "gray"}
          size="compact-xs"
          radius="xl"
          leftSection={<IconArrowsMaximize size={13} />}
          onClick={onFitWidth}
          px="sm"
        >
          Fit width
        </Button>
      </Tooltip>
      <Box style={{ width: 1, height: 18, background: "var(--mantine-color-default-border)" }} />
      <Group gap={2} wrap="nowrap" align="center">
        <Tooltip label="Zoom out" withArrow>
          <ActionIcon
            variant="subtle"
            color="gray"
            size="sm"
            radius="xl"
            onClick={onZoomOut}
            disabled={zoom <= zoomMin}
            aria-label="Zoom out"
          >
            <IconZoomOut size={15} stroke={2} />
          </ActionIcon>
        </Tooltip>
        <Text size="11px" fw={700} w={40} ta="center" ff="monospace">
          {Math.round(zoom * 100)}%
        </Text>
        <Tooltip label="Zoom in" withArrow>
          <ActionIcon
            variant="subtle"
            color="gray"
            size="sm"
            radius="xl"
            onClick={onZoomIn}
            disabled={zoom >= zoomMax}
            aria-label="Zoom in"
          >
            <IconZoomIn size={15} stroke={2} />
          </ActionIcon>
        </Tooltip>
      </Group>
    </Box>
  );
}

function SourceStage({
  filename,
  pageRange,
  compact = false,
  inDrawer = false,
}: {
  filename?: string;
  pageRange?: { from: number; to: number };
  compact?: boolean;
  inDrawer?: boolean;
}) {
  const shortName = filename?.replace(/\.[^.]+$/, "") ?? "Your source";

  return (
    <Center h={compact || inDrawer ? "auto" : "100%"} px="xl" py={compact || inDrawer ? "xl" : 0}>
      <Stack align="center" gap="lg" maw={420}>
        <ThemeIcon size={compact || inDrawer ? 52 : 72} radius="xl" variant="light" color="lavender">
          <IconFileText size={compact || inDrawer ? 26 : 36} stroke={1.5} />
        </ThemeIcon>
        <Stack gap={6} align="center">
          <Title order={compact || inDrawer ? 4 : 3} ta="center" fw={600}>
            {shortName}
          </Title>
          {pageRange && (
            <Text size="sm" c="dimmed" ta="center">
              Pages {pageRange.from}–{pageRange.to}
            </Text>
          )}
          <Text size="sm" c="dimmed" ta="center" lh={1.6}>
            Reference material for this question set. The question always comes first.
          </Text>
        </Stack>
      </Stack>
    </Center>
  );
}

function McqHeroPanel({
  stem,
  options,
  selected,
  onSelect,
  feedback,
  mcqLoading,
  artifactStatus,
  indexProgress,
  hasQuestion,
  queue,
  mode,
  gradeState,
  submitting,
  compact = false,
  canReview = false,
  onReviewPrevious,
  onSubmit,
  onContinue,
  onAdvance,
  onRetry,
}: {
  stem: string;
  options: string[];
  selected: string | null;
  onSelect: (value: string) => void;
  feedback: string | null;
  mcqLoading: boolean;
  artifactStatus?: string;
  indexProgress?: number;
  hasQuestion?: boolean;
  queue?: McqState | null;
  mode: "learn" | "test";
  gradeState: { correct: boolean; correctIndex: number } | null;
  submitting: boolean;
  compact?: boolean;
  canReview?: boolean;
  onReviewPrevious?: () => void;
  onSubmit: () => void;
  onContinue: () => void;
  onAdvance?: () => void;
  onRetry?: () => void;
}) {
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  const safeOptions = normalizeMcqOptions(options);
  const graded = gradeState !== null;
  const showNextQuestion = graded;
  const optionsLocked = graded && (mode === "test" || gradeState.correct);
  // Learn vs Test, the core distinction: Learn reveals the answer + explanation
  // right away (and lets you retry); Test records your choice silently and grades
  // everything at the very end — no peeking. `reveal` gates every "show the answer"
  // affordance so the two modes genuinely feel different.
  const isTest = mode === "test";
  const reveal = graded && !isTest;
  const accent = isTest ? "forest" : "lavender";
  // "Checking" = answer submitted, grade not back yet. We light up the chosen option
  // with a calm pulse so the wait never feels frozen.
  const checking = submitting && !graded;
  const waiting =
    mcqLoading ||
    artifactStatus === "indexing" ||
    !hasQuestion ||
    (Boolean(queue?.generation_pending) && !queue?.current_assertion_id);

  const [statusTick, setStatusTick] = useState(0);
  const stagnant =
    waiting && Boolean(queue?.generation_pending) && (queue?.questions_generated ?? 0) === 0;
  // How long generation has been stuck with 0 questions produced, so we can offer a
  // retry after ~45s. Driven by an interval (not a ref read during render, which can
  // produce stale UI and is a React anti-pattern).
  const [stuckSeconds, setStuckSeconds] = useState(0);
  useEffect(() => {
    if (!stagnant) {
      setStuckSeconds(0);
      return;
    }
    const start = Date.now();
    setStuckSeconds(0);
    const id = window.setInterval(() => {
      setStuckSeconds(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, [stagnant]);
  const waitStatus = learnWaitStatus(
    {
      artifactStatus,
      indexProgress,
      mcqLoading,
      generationPending: queue?.generation_pending,
      pageTriageComplete: queue?.page_triage_complete,
      ragWindowReady: queue?.rag_window_ready,
      questionsGenerated: queue?.questions_generated,
      questionBudget: queue?.question_budget,
      poolAvailable: queue?.pool_available,
    },
    statusTick,
  );
  useInterval(() => {
    if (waiting) setStatusTick((t) => t + 1);
  }, 1200);
  useEffect(() => {
    setStatusTick(0);
  }, [waitStatus.rotateKey]);

  // Keyboard: A–D (or 1–4) to pick an option, Enter to check / advance.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (waiting) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "Enter") {
        if (graded) {
          e.preventDefault();
          onContinue();
        } else if (selected !== null && !submitting) {
          e.preventDefault();
          onSubmit();
        }
        return;
      }
      const k = e.key.toLowerCase();
      let idx = "abcd".indexOf(k);
      if (idx < 0 && /[1-9]/.test(k)) idx = Number(k) - 1;
      if (idx >= 0 && idx < safeOptions.length && !optionsLocked) {
        e.preventDefault();
        onSelect(String(idx));
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [waiting, graded, selected, submitting, optionsLocked, safeOptions.length, onSelect, onSubmit, onContinue]);

  if (waiting) {
    // Determinate progress during generation: turn the vague spinner into a
    // moving bar the user can watch fill toward the page's question budget.
    // Known waits feel ~30% shorter than unknown waits (HCI research). Falls
    // back to an animated indeterminate bar when no budget is known yet.
    const generated = queue?.questions_generated ?? 0;
    const budget = queue?.question_budget ?? 0;
    const hasDeterminate = budget > 0;
    const progressPct = hasDeterminate ? Math.min(100, Math.round((generated / budget) * 100)) : 0;
    const RING = compact ? 124 : 140;
    const R = RING / 2 - 12;
    const CIRC = 2 * Math.PI * R;
    const center = RING / 2;
    return (
      <Center h="100%" py={compact ? "md" : "lg"}>
        <style>{`
          @keyframes zivo-ring-spin { to { transform: rotate(360deg); } }
          @keyframes zivo-blob-a { 0%,100% { transform: translate(0,0) scale(1); } 50% { transform: translate(9px,-11px) scale(1.16); } }
          @keyframes zivo-blob-b { 0%,100% { transform: translate(0,0) scale(1.06); } 50% { transform: translate(-11px,9px) scale(0.9); } }
          @keyframes zivo-blob-c { 0%,100% { transform: translate(0,0) scale(0.95); } 50% { transform: translate(7px,11px) scale(1.12); } }
          @keyframes zivo-fade-up { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
          @keyframes zivo-bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
          .zivo-load-copy { animation: zivo-fade-up 380ms cubic-bezier(0.32,0.72,0,1) both; }
          @media (prefers-reduced-motion: reduce) {
            .zivo-blob, .zivo-ring-spin, .zivo-bob, .zivo-load-copy { animation: none !important; }
          }
        `}</style>
        <Stack align="center" gap={compact ? "md" : "lg"} maw={340}>
          <Box pos="relative" w={RING} h={RING} style={{ display: "grid", placeItems: "center" }}>
            {/* Colorful aurora — three soft brand-tinted blobs drifting behind the ring */}
            <Box className="zivo-blob" pos="absolute" style={{ inset: -6, borderRadius: "50%", filter: "blur(22px)", background: "radial-gradient(60% 60% at 30% 30%, var(--mantine-color-lavender-4), transparent 70%)", opacity: 0.55, animation: "zivo-blob-a 4.5s ease-in-out infinite" }} />
            <Box className="zivo-blob" pos="absolute" style={{ inset: -6, borderRadius: "50%", filter: "blur(22px)", background: "radial-gradient(55% 55% at 72% 42%, var(--mantine-color-sage-4), transparent 70%)", opacity: 0.5, animation: "zivo-blob-b 5.4s ease-in-out infinite" }} />
            <Box className="zivo-blob" pos="absolute" style={{ inset: -6, borderRadius: "50%", filter: "blur(22px)", background: "radial-gradient(55% 55% at 50% 76%, var(--mantine-color-terracotta-3), transparent 70%)", opacity: 0.45, animation: "zivo-blob-c 5s ease-in-out infinite" }} />
            <svg width={RING} height={RING} viewBox={`0 0 ${RING} ${RING}`} style={{ position: "relative" }}>
              <defs>
                <linearGradient id="zivo-ring-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="var(--mantine-color-lavender-5)" />
                  <stop offset="50%" stopColor="var(--mantine-color-sage-5)" />
                  <stop offset="100%" stopColor="var(--mantine-color-terracotta-5)" />
                </linearGradient>
              </defs>
              <circle cx={center} cy={center} r={R} fill="none" stroke="var(--mantine-color-default-border)" strokeOpacity={0.5} strokeWidth={8} />
              <g
                className={hasDeterminate ? undefined : "zivo-ring-spin"}
                style={hasDeterminate ? undefined : { transformOrigin: `${center}px ${center}px`, animation: "zivo-ring-spin 1.1s linear infinite" }}
              >
                <circle
                  cx={center}
                  cy={center}
                  r={R}
                  fill="none"
                  stroke="url(#zivo-ring-grad)"
                  strokeWidth={8}
                  strokeLinecap="round"
                  strokeDasharray={CIRC}
                  strokeDashoffset={hasDeterminate ? CIRC * (1 - progressPct / 100) : CIRC * 0.72}
                  transform={`rotate(-90 ${center} ${center})`}
                  style={{ transition: "stroke-dashoffset 600ms cubic-bezier(0.32,0.72,0,1)" }}
                />
              </g>
            </svg>
            <Box pos="absolute" style={{ display: "grid", placeItems: "center" }}>
              {hasDeterminate ? (
                <Text
                  fz={compact ? 22 : 26}
                  fw={600}
                  c="var(--mantine-color-text)"
                  style={{ fontFamily: "var(--font-serif), Georgia, serif", letterSpacing: "-0.02em", lineHeight: 1 }}
                >
                  {progressPct}%
                </Text>
              ) : (
                <PetLoader size={compact ? 50 : 58} variant="lavender" />
              )}
            </Box>
          </Box>

          <Stack key={waitStatus.rotateKey} className="zivo-load-copy" gap={4} align="center">
            <Text
              fz={compact ? "md" : "lg"}
              fw={600}
              ta="center"
              c="var(--mantine-color-text)"
              style={{ letterSpacing: "-0.02em", fontFamily: "var(--font-serif), Georgia, serif" }}
            >
              {waitStatus.title}
            </Text>
            <Text size="sm" c="dimmed" ta="center" lh={1.55} maw={290}>
              {waitStatus.detail}
            </Text>
          </Stack>

          <GenerationStages
            artifactStatus={artifactStatus}
            indexProgress={indexProgress}
            ragWindowReady={queue?.rag_window_ready}
            pageTriageComplete={queue?.page_triage_complete}
            generationPending={queue?.generation_pending}
            questionsGenerated={generated}
            questionBudget={budget}
            compact={compact}
            isDark={isDark}
          />

          {stuckSeconds >= 45 && onRetry ? (
            <Stack gap={6} align="center">
              <Text size="sm" c="dimmed" ta="center">
                Something&apos;s taking a while.
              </Text>
              <Button variant="light" size="compact-sm" onClick={onRetry}>
                Retry generation
              </Button>
            </Stack>
          ) : null}
        </Stack>
      </Center>
    );
  }

  return (
    <Stack key={stem} h="100%" gap={0} align="stretch" style={{ overflow: "hidden" }}>
      <style>{`
        @keyframes mcq-rise {
          from { opacity: 0; transform: translateY(14px) scale(0.99); filter: blur(4px); }
          to { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
        }
        /* Premium "focus-pull" entrance on every question swap. Pure CSS keyframes —
           reliable across SSR/strict-mode (framer AnimatePresence stalls here). The
           title leads; options cascade in via per-item animation-delay below. */
        .mcq-q { animation: mcq-rise 460ms cubic-bezier(0.32,0.72,0,1) both; }
        .mcq-opt {
          animation: mcq-rise 460ms cubic-bezier(0.32,0.72,0,1) both;
          transition: transform 160ms cubic-bezier(0.32,0.72,0,1), border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
        }
        .mcq-opt:not(:disabled):hover { transform: translateY(-2px); box-shadow: var(--mantine-shadow-paper); border-color: var(--mantine-color-lavender-4) !important; }
        .mcq-opt:not(:disabled):active { transform: translateY(0); }
        /* Checking: the chosen option breathes while the grade comes back. */
        @keyframes mcq-check-pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(124, 109, 242, 0.0); }
          50% { box-shadow: 0 0 0 4px rgba(124, 109, 242, 0.22); }
        }
        .mcq-opt-checking { animation: mcq-check-pulse 1.05s ease-in-out infinite !important; }
        @keyframes mcq-check-dots { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }
        .mcq-check-dot { animation: mcq-check-dots 1.2s ease-in-out infinite; }
        @media (prefers-reduced-motion: reduce) {
          .mcq-q, .mcq-opt, .mcq-opt-checking, .mcq-check-dot { animation: none !important; }
        }
      `}</style>

      {/* Centered, scrollable content region. The card height is fixed by the
          parent (clamp), so showing feedback or a longer stem reflows WITHIN
          this region instead of resizing the card — the footer below never
          moves and the page no longer jumps. */}
      <Box
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
        }}
      >
      {/* Top-anchored so the question is its own scrollable page. */}
      <Box
        style={{
          width: "100%",
          display: "flex",
          flexDirection: "column",
          gap: compact ? 14 : 20,
        }}
      >
      {/* The stem stays put (sticky) while the options + explanation scroll under it. */}
      <Box
        style={{
          position: "sticky",
          top: 0,
          zIndex: 3,
          flexShrink: 0,
          minHeight: compact ? 56 : 72,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          paddingBottom: 6,
          background: "var(--mantine-color-body)",
        }}
      >
      <Title
        order={2}
        className="mcq-q"
        fw={500}
        lh={1.3}
        ta="center"
        c="var(--mantine-color-text)"
        style={{
          fontFamily: "var(--font-serif), Georgia, serif",
          fontSize: compact ? "clamp(1rem, 4.4vw, 1.3rem)" : "clamp(1.2rem, 2.2vw, 1.9rem)",
          letterSpacing: "-0.01em",
          maxWidth: "min(640px, 100%)",
          marginInline: "auto",
          overflowWrap: "anywhere",
        }}
      >
        {stem}
      </Title>
      </Box>

      <Stack gap={compact ? 8 : 10} mih={0} style={{ flexShrink: 0 }}>
        {safeOptions.map((opt, i) => {
          const value = String(i);
          const isSelected = selected === value;
          // Test mode never reveals correctness per-question — the chosen option just
          // shows as "answered" (its selected tint), graded silently for the end.
          const isCorrectOption = reveal && gradeState.correctIndex === i;
          const isWrongSelected = reveal && !gradeState.correct && isSelected;
          const { border, background, chipBg, chipColor, borderWidth } = mcqOptionChrome(isDark, {
            isSelected,
            isCorrectOption,
            isWrongSelected,
          });
          const dim = optionsLocked && !isCorrectOption && !isWrongSelected;
          const isChecking = checking && isSelected;
          return (
            <UnstyledButton
              key={value}
              className={isChecking ? "mcq-opt mcq-opt-checking" : "mcq-opt"}
              disabled={optionsLocked || checking}
              onClick={() => { if (!optionsLocked && !checking) onSelect(value); }}
              style={{
                animationDelay: `${90 + i * 60}ms`,
                width: "100%",
                borderRadius: 14,
                padding: compact ? "12px 12px" : "14px 16px",
                minHeight: 48,
                border: `${borderWidth}px solid ${border}`,
                background,
                opacity: dim ? 0.6 : 1,
              }}
            >
              <Group wrap="nowrap" align="center" gap={compact ? "sm" : "md"}>
                <Box
                  style={{
                    flexShrink: 0,
                    width: 26,
                    height: 26,
                    borderRadius: 8,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: chipBg,
                    color: chipColor,
                    fontFamily: "var(--font-sans), sans-serif",
                    fontWeight: 700,
                    fontSize: 13,
                    transition: "background 160ms ease, color 160ms ease",
                  }}
                >
                  {isCorrectOption ? (
                    <IconCheck size={15} stroke={2.4} />
                  ) : isWrongSelected ? (
                    <IconX size={15} stroke={2.4} />
                  ) : (
                    String.fromCharCode(65 + i)
                  )}
                </Box>
                <Text
                  size={compact ? "sm" : "md"}
                  lh={1.45}
                  ta="left"
                  style={{ flex: 1, fontSize: compact ? undefined : "1.0625rem", color: "var(--mantine-color-text)" }}
                >
                  {opt}
                </Text>
              </Group>
            </UnstyledButton>
          );
        })}
      </Stack>

      {reveal && feedback ? (
        <McqFeedbackCard
          feedback={feedback}
          isCorrect={gradeState?.correct === true}
          compact={compact}
          isDark={isDark}
        />
      ) : graded && isTest ? (
        <Group justify="center" gap={8} mt={compact ? 8 : 12} style={{ flexShrink: 0 }}>
          <ThemeIcon size={22} radius="xl" variant="light" color="forest">
            <IconCheck size={13} stroke={2.4} />
          </ThemeIcon>
          <Text fz="sm" c="dimmed" fw={500}>
            Answer recorded — you&rsquo;ll see your score at the end
          </Text>
        </Group>
      ) : null}
      </Box>

      {/* Anchored to the bottom of the question area — turns the old dead space into a
          calm, mode-defining strip (and the live test tally). */}
      <Center style={{ marginTop: "auto", paddingTop: compact ? 14 : 22, flexShrink: 0 }}>
        <Box
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 9,
            maxWidth: "100%",
            padding: compact ? "6px 12px" : "7px 16px",
            borderRadius: 999,
            background: isDark ? `var(--mantine-color-${accent}-1)` : `var(--mantine-color-${accent}-0)`,
            border: `1px solid var(--mantine-color-${accent}-${isDark ? 3 : 2})`,
          }}
        >
          {isTest ? <IconClipboardList size={14} stroke={2} style={{ flexShrink: 0, color: `var(--mantine-color-${accent}-${isDark ? 8 : 7})` }} /> : <IconBulb size={14} stroke={2} style={{ flexShrink: 0, color: `var(--mantine-color-${accent}-${isDark ? 8 : 7})` }} />}
          <Text fz="xs" fw={600} c={`var(--mantine-color-${accent}-${isDark ? 9 : 8})`} style={{ letterSpacing: "-0.01em" }}>
            {isTest
              ? `Test · graded at the end${(queue?.question_budget ?? 0) > 0 ? ` · ${queue?.questions_answered ?? 0} of ${queue?.question_budget} answered` : ""}`
              : "Learn · instant feedback after each answer, retry until it clicks"}
          </Text>
        </Box>
      </Center>
      </Box>

      <Stack align="center" gap={8} pt={compact ? "sm" : "md"} style={{ flexShrink: 0 }}>
        {showNextQuestion ? (
          <Button
            radius="xl"
            size="md"
            color={isTest ? "forest" : "sage"}
            maw={compact ? "100%" : 300}
            w="100%"
            loading={submitting}
            onClick={onContinue}
            rightSection={<IconArrowRight size={18} stroke={2} />}
          >
            {isTest ? "Next" : "Next question"}
          </Button>
        ) : (
          <Button
            radius="xl"
            size="md"
            color={accent}
            maw={compact ? "100%" : 300}
            w="100%"
            onClick={onSubmit}
            loading={checking}
            disabled={selected === null}
          >
            {checking ? "Checking…" : isTest ? "Submit answer" : "Check answer"}
          </Button>
        )}
        {onAdvance && (
          <Button radius="xl" size="sm" variant="subtle" onClick={onAdvance}>
            Next page
          </Button>
        )}
        {checking ? (
          <Text size="xs" c="dimmed" ta="center" style={{ opacity: 0.9 }}>
            Checking your answer
            <Text component="span" inherit className="mcq-check-dot">…</Text>
          </Text>
        ) : canReview && onReviewPrevious ? (
          <Button
            variant="subtle"
            color="gray"
            size="compact-sm"
            radius="xl"
            leftSection={<IconHistory size={15} stroke={1.7} />}
            onClick={onReviewPrevious}
          >
            Review previous
          </Button>
        ) : !compact ? (
          <Text size="xs" c="dimmed" ta="center" style={{ opacity: 0.85 }}>
            {showNextQuestion
              ? "Press Enter for the next question"
              : "Press A–D to choose · Enter to check"}
          </Text>
        ) : null}
      </Stack>
    </Stack>
  );
}

/**
 * Read-only review of a previously-answered question. Mirrors McqHeroPanel's calm
 * layout (serif stem, the same option chrome + feedback card) but locks everything
 * and adds step controls so the learner can flip back through what they answered.
 */
function McqReviewView({
  card,
  index,
  total,
  compact = false,
  onPrev,
  onNext,
  onExit,
}: {
  card: AnsweredCard;
  index: number;
  total: number;
  compact?: boolean;
  onPrev?: () => void;
  onNext: () => void;
  onExit: () => void;
}) {
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  const safeOptions = normalizeMcqOptions(card.options);
  const { correct, correctIndex } = card.gradeState;

  return (
    <Stack key={card.assertionId} h="100%" gap={0} align="stretch" style={{ overflow: "hidden" }}>
      <Group
        justify="space-between"
        align="center"
        wrap="nowrap"
        px={4}
        pb={8}
        style={{ flexShrink: 0 }}
      >
        <Group gap={8} wrap="nowrap" align="center" style={{ minWidth: 0 }}>
          <ThemeIcon variant="light" color="lavender" radius="xl" size={26}>
            <IconHistory size={15} stroke={1.8} />
          </ThemeIcon>
          <Text fz="sm" fw={600} c="var(--mantine-color-text)" style={{ whiteSpace: "nowrap" }}>
            Reviewing
            <Text component="span" inherit c="dimmed" fw={500}>
              {"  "}
              {index + 1} of {total}
            </Text>
          </Text>
        </Group>
        <Button
          variant="light"
          color="lavender"
          size="compact-sm"
          radius="xl"
          onClick={onExit}
          rightSection={<IconArrowRight size={15} stroke={2} />}
        >
          Back to question
        </Button>
      </Group>

      <Box style={{ flex: 1, minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column" }}>
        <Box style={{ width: "100%", display: "flex", flexDirection: "column", gap: compact ? 14 : 18 }}>
          <Title
            order={2}
            fw={500}
            lh={1.3}
            ta="center"
            c="var(--mantine-color-text)"
            style={{
              fontFamily: "var(--font-serif), Georgia, serif",
              fontSize: compact ? "clamp(1rem, 4.4vw, 1.3rem)" : "clamp(1.2rem, 2.2vw, 1.9rem)",
              letterSpacing: "-0.01em",
              maxWidth: "min(640px, 100%)",
              marginInline: "auto",
              overflowWrap: "anywhere",
            }}
          >
            {card.stem}
          </Title>

          <Stack gap={compact ? 8 : 10} mih={0} style={{ flexShrink: 0 }}>
            {safeOptions.map((opt, i) => {
              const isCorrectOption = correctIndex === i;
              const isWrongSelected = !correct && card.selectedIndex === i;
              const { border, background, chipBg, chipColor, borderWidth } = mcqOptionChrome(isDark, {
                isSelected: card.selectedIndex === i,
                isCorrectOption,
                isWrongSelected,
              });
              const dim = !isCorrectOption && !isWrongSelected;
              return (
                <Box
                  key={i}
                  style={{
                    width: "100%",
                    borderRadius: 14,
                    padding: compact ? "12px 12px" : "14px 16px",
                    minHeight: 48,
                    border: `${borderWidth}px solid ${border}`,
                    background,
                    opacity: dim ? 0.6 : 1,
                  }}
                >
                  <Group wrap="nowrap" align="center" gap={compact ? "sm" : "md"}>
                    <Box
                      style={{
                        flexShrink: 0,
                        width: 26,
                        height: 26,
                        borderRadius: 8,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: chipBg,
                        color: chipColor,
                        fontFamily: "var(--font-sans), sans-serif",
                        fontWeight: 700,
                        fontSize: 13,
                      }}
                    >
                      {isCorrectOption ? (
                        <IconCheck size={15} stroke={2.4} />
                      ) : isWrongSelected ? (
                        <IconX size={15} stroke={2.4} />
                      ) : (
                        String.fromCharCode(65 + i)
                      )}
                    </Box>
                    <Text
                      size={compact ? "sm" : "md"}
                      lh={1.45}
                      ta="left"
                      style={{ flex: 1, fontSize: compact ? undefined : "1.0625rem", color: "var(--mantine-color-text)" }}
                    >
                      {opt}
                    </Text>
                  </Group>
                </Box>
              );
            })}
          </Stack>

          {card.feedback ? (
            <McqFeedbackCard feedback={card.feedback} isCorrect={correct} compact={compact} isDark={isDark} />
          ) : null}
        </Box>
      </Box>

      <Group justify="center" gap={8} pt={compact ? "sm" : "md"} style={{ flexShrink: 0 }}>
        <Button
          variant="default"
          radius="xl"
          size="sm"
          leftSection={<IconArrowLeft size={16} stroke={2} />}
          onClick={onPrev}
          disabled={!onPrev}
        >
          Previous
        </Button>
        <Button
          variant="default"
          radius="xl"
          size="sm"
          rightSection={<IconArrowRight size={16} stroke={2} />}
          onClick={onNext}
        >
          {index + 1 < total ? "Next" : "Back to question"}
        </Button>
      </Group>
    </Stack>
  );
}

function AssistantLogo({ size }: { size: number }) {
  return <BrandMark showWord={false} height={size} />;
}

function TutorPanel({
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
const READ_CHAT_SUGGESTIONS = [
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

function clampPanel(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function pagesInRange(from: number, to: number): number[] {
  const lo = Math.min(from, to);
  const hi = Math.max(from, to);
  const pages: number[] = [];
  for (let p = lo; p <= hi; p += 1) pages.push(p);
  return pages;
}

function formatSelectionSummary(selectedPages: number[], pageCount: number): string {
  if (selectedPages.length === 0) return `No pages selected · ${pageCount} total`;
  if (selectedPages.length === 1) return `1 page selected · page ${selectedPages[0]}`;
  if (selectedPages.length === pageCount) return `All ${pageCount} pages selected`;
  const first = selectedPages[0];
  const last = selectedPages[selectedPages.length - 1];
  const contiguous = selectedPages.length === last - first + 1;
  if (contiguous) return `${selectedPages.length} pages selected · ${first}–${last}`;
  return `${selectedPages.length} pages selected`;
}

function PageSelectionScreen({
  subtitle,
  pageCount,
  sliderFrom,
  sliderTo,
  sliderMarks,
  selectedPages,
  isDark,
  isPdf,
  pdfDoc,
  pdfLoading,
  pdfError,
  thumbCanvasRefs,
  confirming,
  setupError,
  confirmLabel,
  isCompact,
  onRangeChange,
  onPageToggle,
  onSelectAll,
  onClearAll,
  onConfirm,
}: {
  subtitle: string;
  pageCount: number;
  sliderFrom: number;
  sliderTo: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  selectedPages: number[];
  isDark: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  pdfLoading: boolean;
  pdfError: string | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  confirming: boolean;
  setupError: string | null;
  confirmLabel: string;
  isCompact: boolean;
  onRangeChange: (from: number, to: number) => void;
  onPageToggle: (page: number, shiftKey: boolean) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onConfirm: () => void;
}) {
  const bleed = shellBleedPx(isCompact);

  return (
    <Box
      flex={1}
      mih={0}
      h="100%"
      bg="var(--mantine-color-body)"
      my={isCompact ? "calc(-1 * var(--mantine-spacing-xs))" : undefined}
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        overflow: "hidden",
        ...(bleed > 0
          ? {
              margin: -bleed,
              width: `calc(100% + ${bleed * 2}px)`,
              height: `calc(100% + ${bleed * 2}px)`,
            }
          : {}),
        boxSizing: "border-box",
      }}
    >
      {pdfError && (
        <Text c="terracotta.7" size="sm" px="md" pt="xs">
          {pdfError}
        </Text>
      )}
      {!isPdf && !pdfLoading && (
        <Text c="dimmed" size="sm" px="md" pt="xs">
          PDF thumbnails load automatically. Use the slider below ({pageCount} pages).
        </Text>
      )}

      <Box flex={1} mih={0} style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <PageSelectionBody
          padX={isCompact ? SELECTION_PAD_X_COMPACT : SELECTION_PAD_X}
          subtitle={subtitle}
          pdfLoading={pdfLoading}
          pageCount={pageCount}
          sliderFrom={sliderFrom}
          sliderTo={sliderTo}
          sliderMarks={sliderMarks}
          selectedPages={selectedPages}
          isDark={isDark}
          isPdf={isPdf}
          pdfDoc={pdfDoc}
          thumbCanvasRefs={thumbCanvasRefs}
          confirming={confirming}
          setupError={setupError}
          confirmLabel={confirmLabel}
          onRangeChange={onRangeChange}
          onPageToggle={onPageToggle}
          onSelectAll={onSelectAll}
          onClearAll={onClearAll}
          onConfirm={onConfirm}
        />
      </Box>
    </Box>
  );
}

function PageSelectionBody({
  padX,
  subtitle,
  pdfLoading,
  pageCount,
  sliderFrom,
  sliderTo,
  sliderMarks,
  selectedPages,
  isDark,
  isPdf,
  pdfDoc,
  thumbCanvasRefs,
  confirming,
  setupError,
  confirmLabel,
  onRangeChange,
  onPageToggle,
  onSelectAll,
  onClearAll,
  onConfirm,
}: {
  padX: number;
  subtitle?: string;
  pdfLoading?: boolean;
  pageCount: number;
  sliderFrom: number;
  sliderTo: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  selectedPages: number[];
  isDark: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  confirming: boolean;
  setupError: string | null;
  confirmLabel: string;
  onRangeChange: (from: number, to: number) => void;
  onPageToggle: (page: number, shiftKey: boolean) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onConfirm: () => void;
}) {
  const isCompact = useMediaQuery(STUDY_COMPACT_BP);
  const sliderColor = isDark ? "blue.4" : "blue.6";
  const trackBg = isDark ? "var(--mantine-color-dark-3)" : "var(--mantine-color-gray-3)";

  return (
    <Box
      pos="relative"
      flex={1}
      mih={0}
      h="100%"
      style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}
    >
      <Box
        flex={1}
        mih={0}
        px={padX}
        style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}
      >
        <PageThumbnailGrid
          pageCount={pageCount}
          selectedPages={selectedPages}
          isDark={isDark}
          isPdf={isPdf}
          pdfDoc={pdfDoc}
          thumbCanvasRefs={thumbCanvasRefs}
          dockReserve={isCompact ? SELECTION_DOCK_RESERVE_COMPACT : SELECTION_DOCK_RESERVE}
          onPageToggle={onPageToggle}
        />
      </Box>
      <PageSelectionDock
        subtitle={subtitle}
        pdfLoading={pdfLoading}
        sliderFrom={sliderFrom}
        sliderTo={sliderTo}
        pageCount={pageCount}
        sliderMarks={sliderMarks}
        selectedPages={selectedPages}
        isDark={isDark}
        sliderColor={sliderColor}
        trackBg={trackBg}
        confirming={confirming}
        setupError={setupError}
        confirmLabel={confirmLabel}
        onRangeChange={onRangeChange}
        onSelectAll={onSelectAll}
        onClearAll={onClearAll}
        onConfirm={onConfirm}
      />
    </Box>
  );
}

function PageSelectionDock({
  subtitle,
  pdfLoading,
  sliderFrom,
  sliderTo,
  pageCount,
  sliderMarks,
  selectedPages,
  isDark,
  sliderColor,
  trackBg,
  confirming,
  setupError,
  confirmLabel,
  onRangeChange,
  onSelectAll,
  onClearAll,
  onConfirm,
}: {
  subtitle?: string;
  pdfLoading?: boolean;
  sliderFrom: number;
  sliderTo: number;
  pageCount: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  selectedPages: number[];
  isDark: boolean;
  sliderColor: string;
  trackBg: string;
  confirming: boolean;
  setupError: string | null;
  confirmLabel: string;
  onRangeChange: (from: number, to: number) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onConfirm: () => void;
}) {
  const isCompact = useMediaQuery(STUDY_COMPACT_BP);
  const dockStacked = useMediaQuery(STUDY_OVERLAY_BP, false, { getInitialValueInEffect: true });
  const hasSelection = selectedPages.length > 0;
  const panelBorder = isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)";
  const hairline = isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)";
  const secondaryBorder = isDark ? "var(--mantine-color-dark-3)" : "var(--mantine-color-gray-4)";
  const inputBorder = isDark ? "var(--mantine-color-dark-3)" : "var(--mantine-color-gray-4)";
  const inputBg = isDark ? "var(--mantine-color-dark-8)" : "var(--mantine-color-white)";
  const markColor = isDark ? "var(--mantine-color-gray-5)" : "var(--mantine-color-gray-5)";
  const markLabelColor = isDark ? "var(--mantine-color-gray-5)" : "var(--mantine-color-gray-6)";
  const summary = formatSelectionSummary(selectedPages, pageCount);
  const dockMarks = useMemo(
    () =>
      isCompact
        ? [
            { value: 1, label: "1" },
            ...(pageCount > 1 ? [{ value: pageCount, label: String(pageCount) }] : []),
          ]
        : sliderMarks,
    [isCompact, sliderMarks, pageCount],
  );

  const numberInputStyles = {
    input: {
      border: `1px solid ${inputBorder}`,
      backgroundColor: inputBg,
      color: isDark ? "var(--mantine-color-gray-1)" : undefined,
      minHeight: 28,
      height: 28,
      fontSize: 13,
      fontWeight: 500,
      paddingInline: 8,
      textAlign: "center" as const,
    },
  } as const;

  const secondaryButtonStyles = {
    root: {
      border: `1px solid ${secondaryBorder}`,
      backgroundColor: isDark ? "var(--mantine-color-dark-8)" : "var(--mantine-color-white)",
      color: isDark ? "var(--mantine-color-gray-2)" : undefined,
      fontWeight: 600,
      flexShrink: 0,
      whiteSpace: "nowrap" as const,
    },
  } as const;

  const sliderStyles = {
    root: { paddingTop: 0, paddingBottom: isCompact ? 2 : 16, overflow: "visible" },
    track: { backgroundColor: trackBg, height: isCompact ? 3 : 4, borderRadius: 4 },
    bar: {
      backgroundColor: isDark ? "var(--mantine-color-blue-5)" : "var(--mantine-color-blue-6)",
      borderRadius: 4,
    },
    thumb: {
      backgroundColor: isDark ? "var(--mantine-color-gray-0)" : "var(--mantine-color-white)",
      borderColor: isDark ? "var(--mantine-color-blue-4)" : "var(--mantine-color-blue-6)",
      borderWidth: 2,
      boxShadow: isDark
        ? "0 1px 4px rgba(0, 0, 0, 0.45), 0 0 0 0.5px rgba(255, 255, 255, 0.08)"
        : "0 1px 4px rgba(0, 0, 0, 0.18), 0 0 0 0.5px rgba(0, 0, 0, 0.04)",
    },
    mark: {
      width: 3,
      height: 3,
      borderWidth: 0,
      backgroundColor: markColor,
      opacity: 0.7,
    },
    markLabel: {
      color: markLabelColor,
      marginTop: 4,
      fontSize: isCompact ? 9 : 10,
      fontWeight: 500,
      letterSpacing: "-0.01em",
    },
  } as const;

  const rangeSlider = (
    <RangeSlider
      color={sliderColor}
      min={1}
      max={pageCount}
      minRange={1}
      step={1}
      value={[sliderFrom, sliderTo]}
      onChange={([from, to]) => onRangeChange(from, to)}
      marks={dockMarks}
      label={(v) => `Page ${v}`}
      thumbSize={isCompact ? 16 : 20}
      thumbFromLabel="Start page"
      thumbToLabel="End page"
      thumbValueText={(v) => `Page ${v}`}
      restrictToMarks={!isCompact && pageCount <= 12}
      styles={sliderStyles}
    />
  );

  const confirmButton = (
    <Button
      size={isCompact ? "sm" : "md"}
      radius="xl"
      variant="filled"
      color="lavender"
      px={isCompact ? "lg" : "xl"}
      fw={600}
      fullWidth={isCompact}
      h={isCompact ? 40 : undefined}
      rightSection={<IconArrowRight size={isCompact ? 15 : 18} stroke={2.25} />}
      onClick={onConfirm}
      loading={confirming}
      disabled={!hasSelection}
      style={{ flexShrink: 0, whiteSpace: "nowrap" }}
    >
      {confirmLabel}
    </Button>
  );

  const selectionActions = (
    <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
      <Button variant="default" size="sm" radius="xl" onClick={onSelectAll} styles={secondaryButtonStyles}>
        All
      </Button>
      <Button
        variant="default"
        size="sm"
        radius="xl"
        onClick={onClearAll}
        disabled={!hasSelection}
        styles={secondaryButtonStyles}
      >
        Clear
      </Button>
    </Group>
  );

  if (isCompact) {
    return (
      <Box
        pos="absolute"
        left={0}
        right={0}
        bottom={0}
        bg={isDark ? "dark.7" : "gray.0"}
        style={{
          zIndex: 2,
          pointerEvents: "none",
          borderTop: `1px solid ${hairline}`,
          paddingBottom: "calc(12px + env(safe-area-inset-bottom))",
          boxShadow: isDark
            ? "0 -10px 32px rgba(0, 0, 0, 0.4)"
            : "0 -6px 20px rgba(0, 0, 0, 0.08)",
        }}
      >
        <Stack gap={8} px="md" pt={10} pb={4} style={{ pointerEvents: "auto" }}>
          {rangeSlider}
          <Group justify="space-between" align="center" wrap="nowrap" gap="sm">
            <Box miw={0} style={{ flex: 1 }}>
              {subtitle && (
                <Group gap={6} wrap="nowrap" mb={2}>
                  <Text
                    size="xs"
                    c="dimmed"
                    lineClamp={1}
                    tt="uppercase"
                    fw={600}
                    style={{ letterSpacing: "0.05em", fontSize: 10 }}
                  >
                    {subtitle}
                  </Text>
                  {pdfLoading && <Loader size="xs" />}
                </Group>
              )}
              <Text
                fw={600}
                size="sm"
                lineClamp={1}
                c={hasSelection ? "var(--mantine-color-text)" : "dimmed"}
              >
                {summary}
              </Text>
            </Box>
            <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
              <Button variant="subtle" size="compact-sm" radius="xl" onClick={onSelectAll} px="sm">
                All
              </Button>
              <Button
                variant="subtle"
                size="compact-sm"
                radius="xl"
                onClick={onClearAll}
                disabled={!hasSelection}
                px="sm"
                style={{ flexShrink: 0, whiteSpace: "nowrap" }}
              >
                Clear
              </Button>
            </Group>
          </Group>
          {confirmButton}
          {setupError && (
            <Text size="xs" c="terracotta.7" ta="center">
              {setupError}
            </Text>
          )}
        </Stack>
      </Box>
    );
  }

  return (
    <Box
      pos="absolute"
      left={0}
      right={0}
      bottom={0}
      w={dockStacked ? "92%" : SELECTION_DOCK_WIDTH}
      mx="auto"
      pb="md"
      pt="xs"
      style={{ zIndex: 2, pointerEvents: "none" }}
    >
      <Paper
        withBorder
        radius="lg"
        w="100%"
        bg={isDark ? "dark.7" : "gray.0"}
        style={{
          pointerEvents: "auto",
          borderColor: panelBorder,
          boxShadow: isDark
            ? "0 -12px 40px rgba(0, 0, 0, 0.45), 0 0 0 1px rgba(255, 255, 255, 0.04) inset"
            : "0 -8px 32px rgba(0, 0, 0, 0.08), 0 0 0 1px rgba(255, 255, 255, 0.6) inset",
        }}
      >
      <Stack gap={0}>
        <Box px={isCompact ? "sm" : "md"} pt="sm" pb={isCompact ? "xs" : 4}>
          {isCompact ? (
            <Stack gap="sm">
              <Group justify="center" gap={8} wrap="nowrap">
                <Text size="xs" c="dimmed" fw={500}>
                  From
                </Text>
                <NumberInput
                  hideControls
                  size="xs"
                  w={56}
                  radius="md"
                  min={1}
                  max={pageCount}
                  value={sliderFrom}
                  onChange={(v) => onRangeChange(typeof v === "number" ? v : 1, sliderTo)}
                  styles={numberInputStyles}
                />
                <Text size="xs" c="dimmed" fw={500}>
                  To
                </Text>
                <NumberInput
                  hideControls
                  size="xs"
                  w={56}
                  radius="md"
                  min={sliderFrom}
                  max={pageCount}
                  value={sliderTo}
                  onChange={(v) => onRangeChange(sliderFrom, typeof v === "number" ? v : sliderFrom)}
                  styles={numberInputStyles}
                />
              </Group>
              {rangeSlider}
            </Stack>
          ) : (
            <Group align="center" gap="md" wrap="nowrap" w="100%">
              <Group gap={6} align="center" wrap="nowrap" style={{ flexShrink: 0 }}>
                <Text size="xs" c="dimmed" fw={500}>
                  From
                </Text>
                <NumberInput
                  hideControls
                  size="xs"
                  w={52}
                  radius="md"
                  min={1}
                  max={pageCount}
                  value={sliderFrom}
                  onChange={(v) => onRangeChange(typeof v === "number" ? v : 1, sliderTo)}
                  styles={numberInputStyles}
                />
                <Text size="xs" c="dimmed" fw={500}>
                  To
                </Text>
                <NumberInput
                  hideControls
                  size="xs"
                  w={52}
                  radius="md"
                  min={sliderFrom}
                  max={pageCount}
                  value={sliderTo}
                  onChange={(v) => onRangeChange(sliderFrom, typeof v === "number" ? v : sliderFrom)}
                  styles={numberInputStyles}
                />
              </Group>
              <Box flex={1} miw={120} style={{ minWidth: 0 }}>
                {rangeSlider}
              </Box>
            </Group>
          )}
        </Box>

        <Box h={1} bg={hairline} />

        <Box px={isCompact ? "sm" : "md"} py={isCompact ? "xs" : "sm"}>
          <Stack gap={isCompact ? "sm" : "xs"}>
            {isCompact ? (
              <Stack gap={6} align="center">
                {subtitle && (
                  <Group gap="xs" wrap="nowrap" justify="center">
                    <Text
                      size="xs"
                      c="dimmed"
                      ta="center"
                      lineClamp={2}
                      tt="uppercase"
                      fw={600}
                      style={{ letterSpacing: "0.04em" }}
                    >
                      {subtitle}
                    </Text>
                    {pdfLoading && <Loader size="xs" />}
                  </Group>
                )}
                <Text
                  fw={600}
                  size="sm"
                  ta="center"
                  lineClamp={2}
                  c={hasSelection ? "var(--mantine-color-text)" : "dimmed"}
                >
                  {summary}
                </Text>
                <Text size="xs" c="dimmed" ta="center">
                  Tap a page · Shift+tap to extend
                </Text>
                <Group grow gap="xs" w="100%">
                  <Button variant="default" size="sm" radius="xl" onClick={onSelectAll} styles={secondaryButtonStyles}>
                    All
                  </Button>
                  <Button
                    variant="default"
                    size="sm"
                    radius="xl"
                    onClick={onClearAll}
                    disabled={!hasSelection}
                    styles={secondaryButtonStyles}
                  >
                    Clear
                  </Button>
                </Group>
                {confirmButton}
              </Stack>
            ) : dockStacked ? (
              <Stack gap="sm">
                <Group align="flex-start" justify="space-between" gap="md" wrap="nowrap" w="100%">
                  <Box miw={0} style={{ flex: 1 }}>
                    {subtitle && (
                      <Group gap="xs" wrap="nowrap" align="center">
                        <Text
                          size="xs"
                          c="dimmed"
                          lineClamp={2}
                          tt="uppercase"
                          fw={600}
                          style={{ letterSpacing: "0.04em" }}
                        >
                          {subtitle}
                        </Text>
                        {pdfLoading && <Loader size="xs" />}
                      </Group>
                    )}
                  </Box>
                  <Stack gap={2} align="flex-end" miw={0} style={{ flex: 1.2 }}>
                    <Text
                      fw={600}
                      size="sm"
                      ta="right"
                      lineClamp={2}
                      c={hasSelection ? "var(--mantine-color-text)" : "dimmed"}
                    >
                      {summary}
                    </Text>
                    <Text size="xs" c="dimmed" ta="right" lineClamp={1}>
                      Tap a page · Shift+tap to extend
                    </Text>
                  </Stack>
                </Group>
                <Group gap="xs" wrap="nowrap" justify="flex-end" w="100%">
                  {selectionActions}
                  {confirmButton}
                </Group>
              </Stack>
            ) : (
              <Group align="center" wrap="nowrap" gap="lg" w="100%" justify="space-between">
                <Box miw={0} style={{ flex: 1, overflow: "hidden" }}>
                  {subtitle && (
                    <Group gap="xs" wrap="nowrap" align="center">
                      <Text
                        size="xs"
                        c="dimmed"
                        lineClamp={1}
                        tt="uppercase"
                        fw={600}
                        style={{ letterSpacing: "0.04em" }}
                      >
                        {subtitle}
                      </Text>
                      {pdfLoading && <Loader size="xs" />}
                    </Group>
                  )}
                </Box>

                <Stack gap={2} align="center" miw={0} style={{ flex: 1.2, overflow: "hidden" }}>
                  <Text
                    fw={600}
                    size="sm"
                    ta="center"
                    lineClamp={1}
                    c={hasSelection ? "var(--mantine-color-text)" : "dimmed"}
                  >
                    {summary}
                  </Text>
                  <Text size="xs" c="dimmed" ta="center" lineClamp={1}>
                    Tap a page · Shift+tap to extend
                  </Text>
                </Stack>

                <Group gap="xs" wrap="nowrap" justify="flex-end" style={{ flexShrink: 0 }}>
                  {selectionActions}
                  {confirmButton}
                </Group>
              </Group>
            )}
            {setupError && (
              <Text size="xs" c="terracotta.7" ta={isCompact ? "center" : undefined}>
                {setupError}
              </Text>
            )}
          </Stack>
        </Box>
      </Stack>
    </Paper>
    </Box>
  );
}

function PageThumbnailGrid({
  pageCount,
  selectedPages,
  isDark,
  isPdf,
  pdfDoc,
  thumbCanvasRefs,
  dockReserve = 0,
  onPageToggle,
}: {
  pageCount: number;
  selectedPages: number[];
  isDark: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  dockReserve?: number;
  onPageToggle: (page: number, shiftKey: boolean) => void;
}) {
  const selectedSet = useMemo(() => new Set(selectedPages), [selectedPages]);
  const outerRef = useRef<HTMLDivElement | null>(null);
  const [gridWidth, setGridWidth] = useState(0);
  const isCompact = useMediaQuery(STUDY_COMPACT_BP);

  const compactGrid = isCompact || (gridWidth > 0 && gridWidth < 520);
  const gap = compactGrid ? THUMB_GAP_COMPACT : THUMB_GAP;
  const thumbMinWidth = compactGrid ? THUMB_MIN_WIDTH_COMPACT : THUMB_MIN_WIDTH;
  const maxCols = compactGrid ? THUMB_MAX_COLS_COMPACT : THUMB_MAX_COLS;
  const { cols, thumbWidth } = computeGridLayout(gridWidth, thumbMinWidth, gap, maxCols);
  const renderThumbWidth =
    compactGrid && gridWidth > 0
      ? Math.floor((gridWidth - gap * Math.max(0, cols - 1)) / cols)
      : thumbWidth;
  const stableThumbWidth = bucketPdfThumbWidth(renderThumbWidth);
  const cellThumbWidth =
    compactGrid && renderThumbWidth > 0 ? renderThumbWidth : stableThumbWidth;
  const [scrollRoot, setScrollRoot] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    const id = "zivo-page-scroll-style";
    if (document.getElementById(id)) return;
    const el = document.createElement("style");
    el.id = id;
    el.textContent = "[data-zivo-page-scroll]::-webkit-scrollbar{display:none;width:0;height:0}";
    document.head.appendChild(el);
  }, []);

  useEffect(() => {
    const el = outerRef.current;
    if (!el) return;
    const update = () => setGridWidth(el.clientWidth);
    update();
    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pageNumbers = useMemo(
    () => Array.from({ length: pageCount }, (_, index) => index + 1),
    [pageCount],
  );

  const gridContent = (
    <Box
      w="100%"
      pb={(compactGrid ? SELECTION_PAD_Y_COMPACT : SELECTION_PAD_Y) + dockReserve}
      style={{
        display: "grid",
        gridTemplateColumns: compactGrid
          ? `repeat(${cols}, minmax(0, 1fr))`
          : `repeat(${cols}, ${thumbWidth}px)`,
        justifyContent: compactGrid ? "stretch" : "center",
        gap,
        boxSizing: "border-box",
      }}
    >
      {pageNumbers.map((page) => (
        <PageThumbnailCell
          key={page}
          page={page}
          selected={selectedSet.has(page)}
          isDark={isDark}
          isPdf={isPdf}
          pdfDoc={pdfDoc}
          thumbWidth={cellThumbWidth}
          compact={compactGrid}
          scrollRoot={scrollRoot}
          thumbCanvasRefs={thumbCanvasRefs}
          onToggle={onPageToggle}
        />
      ))}
    </Box>
  );

  const bindScrollContainer = useCallback((node: HTMLDivElement | null) => {
    setScrollRoot(node);
  }, []);

  return (
    <Box
      ref={outerRef}
      flex={1}
      mih={0}
      w="100%"
      style={{ minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}
    >
      <Box
        ref={bindScrollContainer}
        data-zivo-page-scroll
        flex={1}
        mih={0}
        pt={compactGrid ? 6 : 8}
        style={{
          minHeight: 0,
          overflowY: "auto",
          overflowX: "hidden",
          WebkitOverflowScrolling: "touch",
          overscrollBehavior: "contain",
          scrollbarWidth: "none",
          msOverflowStyle: "none",
        }}
      >
        {gridContent}
      </Box>
    </Box>
  );
}

function PageThumbnailCell({
  page,
  selected,
  isDark,
  isPdf,
  pdfDoc,
  thumbWidth,
  compact,
  scrollRoot,
  thumbCanvasRefs,
  onToggle,
}: {
  page: number;
  selected: boolean;
  isDark: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  thumbWidth: number;
  compact?: boolean;
  scrollRoot: HTMLDivElement | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  onToggle: (page: number, shiftKey: boolean) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const [nearViewport, setNearViewport] = useState(false);
  const [renderedSize, setRenderedSize] = useState({ width: 0, height: 0 });
  const [pageAspect, setPageAspect] = useState(
    compact ? THUMB_FRAME_ASPECT_COMPACT : THUMB_FRAME_ASPECT,
  );
  const cellRef = useRef<HTMLButtonElement>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [frameWidth, setFrameWidth] = useState(compact ? 0 : thumbWidth);
  const ringColor = "var(--mantine-color-lavender-6)";
  const idleRing = "var(--mantine-color-default-border)";
  const ringWidth = selected ? 3 : 1;
  const innerRadius = compact ? 12 : 14;
  const outerRadius = innerRadius + ringWidth;
  const renderWidth = compact ? (frameWidth > 0 ? frameWidth : thumbWidth) : thumbWidth;
  const frameHeight = Math.round(renderWidth * pageAspect);
  const rendered =
    renderedSize.width === renderWidth &&
    renderedSize.height === frameHeight &&
    renderWidth > 0;

  useEffect(() => {
    if (!compact) {
      setFrameWidth(thumbWidth);
      return;
    }
    const el = frameRef.current;
    if (!el) return;
    const update = () => setFrameWidth(Math.round(el.clientWidth));
    update();
    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => ro.disconnect();
  }, [compact, thumbWidth]);

  useEffect(() => {
    if (!pdfDoc || !isPdf) return;
    let cancelled = false;
    void (async () => {
      const aspect = await pdfPageAspectRatio(pdfDoc, page);
      if (!cancelled) setPageAspect(aspect);
    })();
    return () => {
      cancelled = true;
    };
  }, [pdfDoc, isPdf, page]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const registry = thumbCanvasRefs.current;
    registry[page] = canvas;
    return () => {
      if (registry[page] === canvas) {
        delete registry[page];
      }
    };
  }, [page, thumbCanvasRefs]);

  useEffect(() => {
    const target = cellRef.current;
    if (!target || !scrollRoot) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        setNearViewport(entry.isIntersecting);
      },
      {
        root: scrollRoot,
        rootMargin: compact ? "360px 0px" : "280px 0px",
        threshold: 0.01,
      },
    );

    observer.observe(target);
    return () => observer.disconnect();
  }, [scrollRoot, compact, page]);

  useEffect(() => {
    if (!nearViewport || !pdfDoc || !isPdf || renderWidth < 1) return;

    let cancelled = false;

    void (async () => {
      const canvas = canvasRef.current;
      if (!canvas || cancelled) return;
      try {
        await renderPdfThumbToCanvas(pdfDoc, page, canvas, renderWidth, frameHeight);
        if (!cancelled) setRenderedSize({ width: renderWidth, height: frameHeight });
      } catch {
        if (!cancelled) return;
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [nearViewport, pdfDoc, isPdf, page, renderWidth, frameHeight]);

  return (
    <UnstyledButton
      ref={cellRef}
      onClick={(event) => onToggle(page, event.shiftKey)}
      aria-label={`Page ${page}${selected ? ", selected" : ""}`}
      aria-pressed={selected}
      w="100%"
      pb={compact ? 4 : 8}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        transition: "transform 140ms ease",
        transform: hovered && !selected ? "translateY(-2px)" : undefined,
      }}
    >
      <Box pos="relative" w="100%">
        <Box
          p={ringWidth}
          bg={selected ? ringColor : idleRing}
          style={{
            borderRadius: outerRadius,
            overflow: "hidden",
            lineHeight: 0,
            transition: "background-color 160ms ease",
          }}
        >
          <Box
            bg={isDark ? "dark.7" : "gray.0"}
            w="100%"
            style={{
              borderRadius: innerRadius,
              overflow: "hidden",
              lineHeight: 0,
              opacity: selected ? 1 : 0.94,
              transition: "opacity 160ms ease",
            }}
          >
            {isPdf ? (
              <Box
                ref={frameRef}
                pos="relative"
                w="100%"
                h={frameHeight}
                style={{
                  borderRadius: innerRadius,
                  overflow: "hidden",
                }}
              >
                {!rendered && (
                  <Center
                    pos="absolute"
                    inset={0}
                    bg={isDark ? "dark.6" : "gray.1"}
                  >
                    <Loader size="xs" color="gray" />
                  </Center>
                )}
                <canvas
                  ref={canvasRef}
                  style={{
                    display: "block",
                    verticalAlign: "top",
                    opacity: rendered ? 1 : 0,
                    transition: "opacity 180ms ease",
                  }}
                />
              </Box>
            ) : (
              <Center h={frameHeight} w="100%">
                <IconFileText size={40} stroke={1.25} color="var(--mantine-color-dimmed)" />
              </Center>
            )}
          </Box>
        </Box>
        <Text
          size={compact ? "sm" : "md"}
          ta="center"
          mt={compact ? 8 : 12}
          fw={selected ? 700 : 500}
          c={selected ? "lavender.7" : "dimmed"}
          lh={1}
        >
          {page}
        </Text>
        {selected && (
          <ThemeIcon
            pos="absolute"
            top={ringWidth + 6}
            right={ringWidth + 6}
            size={32}
            radius="xl"
            color="lavender"
            variant="filled"
            style={{ boxShadow: "0 3px 12px rgba(0,0,0,0.18)" }}
          >
            <IconCheck size={18} stroke={3} />
          </ThemeIcon>
        )}
      </Box>
    </UnstyledButton>
  );
}

function pageSliderPoint(): ReactNode {
  return (
    <Box mt={6}>
      <IconPoint size={10} stroke={1.5} />
    </Box>
  );
}

function buildPageSliderMarks(pageCount: number): { value: number; label?: ReactNode }[] {
  if (pageCount <= 1) {
    return [{ value: 1, label: "1" }];
  }

  if (pageCount <= 12) {
    return Array.from({ length: pageCount }, (_, i) => ({
      value: i + 1,
      label: String(i + 1),
    }));
  }

  const segments = 8;
  const ticks: number[] = [1];
  for (let i = 1; i < segments; i += 1) {
    const v = Math.round(1 + (i / segments) * (pageCount - 1));
    if (ticks[ticks.length - 1] !== v) {
      ticks.push(v);
    }
  }
  if (ticks[ticks.length - 1] !== pageCount) {
    ticks.push(pageCount);
  }

  const marks: { value: number; label?: ReactNode }[] = [];
  for (let i = 0; i < ticks.length; i += 1) {
    marks.push({ value: ticks[i], label: String(ticks[i]) });
    if (i < ticks.length - 1) {
      marks.push({ value: (ticks[i] + ticks[i + 1]) / 2, label: pageSliderPoint() });
    }
  }

  return marks;
}
