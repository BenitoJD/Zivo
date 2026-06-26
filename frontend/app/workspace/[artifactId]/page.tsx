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
  Group,
  Loader,
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
import { useDisclosure, useInterval, useLocalStorage, useMediaQuery, useMounted } from "@mantine/hooks";
import {
  IconArrowRight,
  IconArrowUp,
  IconArrowsMaximize,
  IconCheck,
  IconClipboardList,
  IconFileText,
  IconGripVertical,
  IconMessageCircle,
  IconPlayerStop,
  IconPoint,
  IconX,
  IconZoomIn,
  IconZoomOut,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { apiFetchBytes, apiGet, apiPost, apiPostSSE, apiUrl, ensureGuestSession, humanizeApiFailure, isArtifactId } from "@/lib/api/client";
import {
  queryKeys,
  useArtifactPagesQuery,
  useArtifactQuery,
  useAssertionQuery,
  useChatMessagesQuery,
} from "@/lib/api/queries";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";
import { BrandMark } from "@/app/_components/BrandMark";
import { mcqOptionChrome } from "@/app/_components/mcq/McqCard";
import { AssistantMarkdown } from "@/lib/chatMarkdown";
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
const STUDY_DESKTOP_BP = "(min-width: 48em)";
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
const THUMB_FRAME_ASPECT = 1.38;
const THUMB_FRAME_ASPECT_COMPACT = 1.36;
const THUMB_COVER_ZOOM = 1.08;
const THUMB_COVER_ZOOM_COMPACT = 1.1;

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
  const [mode, setMode] = useState<"learn" | "test">("learn");

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

  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<{ role: string; content: string }[]>([]);
  const [chatBusy, setChatBusy] = useState(false);
  const chatAbortRef = useRef<AbortController | null>(null);
  const [chatThinking, setChatThinking] = useState(false);
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
  const chatMessagesQuery = useChatMessagesQuery(artifactId, !invalidArtifactId);
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

  useEffect(() => {
    if (!chatMessagesQuery.data || chatBusy) return;
    if (chatHydratedRef.current === artifactId) return;
    chatHydratedRef.current = artifactId;
    setChatMessages(
      chatMessagesQuery.data
        .filter((m) => (m.content || "").trim())
        .filter((m) => !(m.role === "assistant" && isTransientChatAssistantMessage(m.content)))
        .map((m) => ({ role: m.role, content: m.content })),
    );
  }, [artifactId, chatBusy, chatMessagesQuery.data]);

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
      });
      es.addEventListener(
        "error",
        () => {
          if (cancelled) return;
          es.close();
          if (queueStreamRef.current === es) queueStreamRef.current = null;
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
    };
  }, [artifactId, invalidArtifactId, studyRangeKey, artifact?.status]);

  useEffect(() => {
    if (invalidArtifactId || !studyRangeKey || artifact?.status !== "ready") return;
    const needsPoll =
      !queue?.current_assertion_id ||
      (Boolean(queue.generation_pending) && (queue.pool_available ?? 0) === 0);
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
    queue?.current_assertion_id,
    queue?.generation_pending,
    queue?.pool_available,
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
      setFeedback(res.feedback ?? (correct ? "Correct!" : "Try again."));
      setGradeState({ correct, correctIndex });
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

  async function sendChat() {
    if (!chatInput.trim() || chatBusy || !chatContextReady) return;
    const userMsg = chatInput.trim();
    setChatInput("");
    setChatBusy(true);
    setChatThinking(true);
    chatAbortRef.current?.abort();
    const abort = new AbortController();
    chatAbortRef.current = abort;
    setChatMessages((m) => [...m, { role: "user", content: userMsg }, { role: "assistant", content: "" }]);
    try {
      await ensureGuestSession();
      const currentPage = queue?.current_page;
      const scope: Record<string, unknown> =
        currentPage && currentPage > 0 ? { current_page: currentPage } : {};
      if (queue?.current_assertion_id) {
        scope.current_assertion_id = queue.current_assertion_id;
      }
      if (gradeState !== null && selected !== null) {
        scope.confirmed_choice_index = Number(selected);
        scope.answer_correct = gradeState.correct;
      }
      let assistant = "";
      let gotToken = false;
      await apiPostSSE("/api/chat", {
        document_id: artifactId,
        message: userMsg,
        scope,
      }, {
        onStatus: (phase) => {
          if (phase === "thinking") setChatThinking(true);
        },
        onChunk: (chunk) => {
          gotToken = true;
          setChatThinking(false);
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
      setChatThinking(false);
    }
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
              <Loader type="oval" size="sm" />
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
        showProgress={!mcqLoading && Boolean(queue?.current_assertion_id) && !showPageComplete && !showDocumentComplete}
        compact={isCompact}
      />
      <Box
        flex={1}
        mih={0}
        px={{ base: "sm", sm: "md", lg: "lg" }}
        pb={{ base: "xs", sm: "md" }}
        style={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          justifyContent: "center",
          minHeight: 0,
        }}
      >
        <Box maw={680} w="100%" mx="auto" mih={0} style={{ maxHeight: "100%", overflow: "hidden" }}>
          {showDocumentComplete && completedRange ? (
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
          ) : (
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
            mode={mode}
            gradeState={gradeState}
            submitting={submitting}
            compact={isCompact}
            onSubmit={() => void submitMcq()}
            onContinue={() => void advanceMcq()}
            onRetry={() => void refreshQueue()}
          />
          )}
        </Box>
      </Box>
    </Box>
  );

  return (
    <Box
      flex={1}
      mih={0}
      h="100%"
      mx={{ base: "calc(-1 * var(--mantine-spacing-xs))", sm: "calc(-1 * var(--mantine-spacing-md))" }}
      my={{ base: "calc(-1 * var(--mantine-spacing-xs))", sm: "calc(-1 * var(--mantine-spacing-md))" }}
      style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}
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
                thinking={chatThinking}
                contextReady={chatContextReady}
                onInputChange={setChatInput}
                onSend={() => void sendChat()}
                onStop={stopChat}
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
              thinking={chatThinking}
              contextReady={chatContextReady}
              onInputChange={setChatInput}
              onSend={() => void sendChat()}
              onStop={stopChat}
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
}: {
  questionIndex: number;
  questionTotal: number;
  mode: "learn" | "test";
  onModeChange: (mode: "learn" | "test") => void;
  showProgress?: boolean;
  compact?: boolean;
}) {
  const showBar = showProgress && questionTotal > 0;
  const pct = showBar ? Math.min(100, Math.round((questionIndex / questionTotal) * 100)) : 0;
  const segmented = showBar && questionTotal <= 16;

  return (
    <Group
      px={compact ? "sm" : { base: "sm", sm: "md", lg: "lg" }}
      py={compact ? 6 : 8}
      justify="space-between"
      align="center"
      wrap="nowrap"
      gap="md"
      style={{ flexShrink: 0 }}
    >
      <Group gap={compact ? 8 : 12} wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
        {showBar && (
          <Text size="xs" c="dimmed" fw={600} ff="monospace" style={{ flexShrink: 0, letterSpacing: "0.02em" }}>
            {String(questionIndex).padStart(2, "0")}
            <Text component="span" inherit style={{ opacity: 0.45 }}>
              {" / "}
              {String(questionTotal).padStart(2, "0")}
            </Text>
          </Text>
        )}
        {segmented ? (
          <Group gap={4} wrap="nowrap" style={{ flex: 1, minWidth: 0, maxWidth: 380 }}>
            {Array.from({ length: questionTotal }).map((_, i) => (
              <Box
                key={i}
                style={{
                  flex: 1,
                  height: 5,
                  borderRadius: 99,
                  background:
                    i < questionIndex
                      ? "var(--mantine-color-lavender-6)"
                      : "var(--mantine-color-gray-3)",
                  transition: "background 260ms ease",
                }}
              />
            ))}
          </Group>
        ) : showBar ? (
          <Box
            style={{
              flex: 1,
              maxWidth: 380,
              height: 5,
              borderRadius: 99,
              background: "var(--mantine-color-gray-3)",
              overflow: "hidden",
            }}
          >
            <Box
              style={{
                width: `${pct}%`,
                height: "100%",
                borderRadius: 99,
                background: "var(--mantine-color-lavender-6)",
                transition: "width 320ms cubic-bezier(0.32,0.72,0,1)",
              }}
            />
          </Box>
        ) : null}
      </Group>
      <StudyModeSwitch mode={mode} onChange={onModeChange} compact={compact} />
    </Group>
  );
}

function StudyModeSwitch({
  mode,
  onChange,
  compact = false,
}: {
  mode: "learn" | "test";
  onChange: (mode: "learn" | "test") => void;
  compact?: boolean;
}) {
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";

  return (
    <SegmentedControl
      size="xs"
      radius="xl"
      value={mode}
      onChange={(v) => onChange(v as "learn" | "test")}
      data={[
        { label: "Learn", value: "learn" },
        { label: "Test", value: "test" },
      ]}
      styles={{
        root: {
          background: isDark ? "var(--mantine-color-dark-6)" : "var(--mantine-color-gray-1)",
          border: `1px solid ${isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)"}`,
        },
        label: {
          fontWeight: 600,
          paddingInline: compact ? 12 : 16,
          fontSize: compact ? 11 : 12,
          letterSpacing: "-0.01em",
        },
        indicator: {
          boxShadow: "none",
        },
      }}
    />
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
  const edgeBorder = "2px solid var(--mantine-color-default-border)";
  const accent = `var(--mantine-color-${color}-filled)`;

  return (
    <Tooltip label={`Open ${label}`} position={side === "left" ? "right" : "left"} withArrow>
      <UnstyledButton
        onClick={onClick}
        aria-label={`Open ${label}`}
        style={{
          position: "absolute",
          top: "50%",
          transform: "translateY(-50%)",
          [side]: 0,
          zIndex: 6,
          minWidth: 52,
          padding: "12px 10px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 5,
          borderRadius: side === "left" ? "0 12px 12px 0" : "12px 0 0 12px",
          borderTop: edgeBorder,
          borderBottom: edgeBorder,
          borderLeft: side === "left" ? "none" : edgeBorder,
          borderRight: side === "right" ? "none" : edgeBorder,
          background: `color-mix(in srgb, ${accent} 14%, var(--mantine-color-body))`,
          color: accent,
        }}
      >
        {icon}
        <Text size="10px" fw={700} tt="uppercase" lh={1} c={color} style={{ letterSpacing: "0.05em" }}>
          {label}
        </Text>
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
          px="sm"
          py={6}
          justify="space-between"
          wrap="nowrap"
          gap="xs"
          style={{
            flexShrink: 0,
            borderBottom: open ? railBorder : undefined,
            minHeight: 36,
            background: "var(--mantine-color-body)",
          }}
        >
          <Text size="xs" fw={600} truncate tt="uppercase" style={{ letterSpacing: "0.04em" }}>
            {title}
          </Text>
          <ActionIcon variant="subtle" color="gray" radius="xl" size="sm" onClick={onClose} aria-label={`Close ${title}`}>
            <IconX size={16} />
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
  const [zoom, setZoom] = useState(1);
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
      setZoom(1);
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

    void (async () => {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
      if (cancelled) return;
      for (const p of displayPages) {
        if (cancelled) return;
        const canvas = canvasRefs.current[p];
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
      cancelAllPdfRenders(Object.values(canvasRefs.current));
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
                          background: isActivePage
                            ? "var(--mantine-color-lavender-7)"
                            : "rgba(35,34,32,0.5)",
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

function McqFeedbackCard({
  feedback,
  isCorrect,
  compact,
  isDark,
}: {
  feedback: string;
  isCorrect: boolean;
  compact?: boolean;
  isDark: boolean;
}) {
  const parts = feedback.split("\n\n").map((p) => p.trim()).filter(Boolean);
  const lead = parts[0] ?? feedback;
  const detail = parts.slice(1).join("\n\n");

  const surface = isCorrect
    ? isDark
      ? "var(--mantine-color-sage-1)"
      : "var(--mantine-color-sage-0)"
    : isDark
      ? "var(--mantine-color-terracotta-1)"
      : "var(--mantine-color-terracotta-0)";
  const outline = isCorrect ? "var(--mantine-color-sage-3)" : "var(--mantine-color-terracotta-3)";
  const primaryText = isCorrect
    ? isDark
      ? "var(--mantine-color-sage-8)"
      : "var(--mantine-color-sage-9)"
    : isDark
      ? "var(--mantine-color-terracotta-8)"
      : "var(--mantine-color-terracotta-9)";
  const secondaryText = isCorrect
    ? isDark
      ? "var(--mantine-color-sage-7)"
      : "var(--mantine-color-sage-8)"
    : isDark
      ? "var(--mantine-color-terracotta-7)"
      : "var(--mantine-color-terracotta-8)";

  return (
    <Box
      className="mcq-feedback"
      style={{
        flexShrink: 0,
        textAlign: "left",
        padding: compact ? "14px 16px" : "16px 18px",
        borderRadius: 14,
        background: surface,
        border: `1px solid ${outline}`,
        maxHeight: compact ? 148 : 184,
        overflow: "auto",
      }}
    >
      <style>{`
        @keyframes mcq-fb { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
        .mcq-feedback { animation: mcq-fb 300ms cubic-bezier(0.32,0.72,0,1) both; }
        @media (prefers-reduced-motion: reduce) { .mcq-feedback { animation: none !important; } }
      `}</style>
      <Group gap={10} wrap="nowrap" align="center" mb={8}>
        <Box
          style={{
            width: 24,
            height: 24,
            borderRadius: "50%",
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: isCorrect ? "var(--mantine-color-sage-6)" : "var(--mantine-color-terracotta-6)",
            color: "#FFFFFF",
          }}
        >
          {isCorrect ? <IconCheck size={14} stroke={2.6} /> : <IconX size={14} stroke={2.6} />}
        </Box>
        <Text fw={700} size={compact ? "sm" : "md"} c={primaryText} style={{ letterSpacing: "-0.01em" }}>
          {isCorrect ? "Correct" : "Not quite"}
        </Text>
      </Group>
      <Text
        size={compact ? "sm" : "md"}
        lh={1.7}
        c={primaryText}
        style={{ whiteSpace: "pre-wrap", fontSize: compact ? undefined : "1.0625rem" }}
      >
        {lead}
      </Text>
      {detail ? (
        <Text
          size={compact ? "sm" : "md"}
          lh={1.7}
          mt={10}
          c={secondaryText}
          style={{ whiteSpace: "pre-wrap", fontSize: compact ? undefined : "1.0625rem" }}
        >
          {detail}
        </Text>
      ) : null}
    </Box>
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
  const waiting =
    mcqLoading ||
    artifactStatus === "indexing" ||
    !hasQuestion ||
    (Boolean(queue?.generation_pending) && !queue?.current_assertion_id);

  const [statusTick, setStatusTick] = useState(0);
  const stagnant =
    waiting && Boolean(queue?.generation_pending) && (queue?.questions_generated ?? 0) === 0;
  const stuckStartRef = useRef<number | null>(null);
  if (stagnant) {
    if (!stuckStartRef.current) stuckStartRef.current = Date.now();
  } else {
    stuckStartRef.current = null;
  }
  const stuckSeconds =
    stagnant && stuckStartRef.current
      ? Math.floor((Date.now() - stuckStartRef.current) / 1000)
      : 0;
  void statusTick;
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
    return (
      <Center py={compact ? "lg" : "xl"}>
        <Stack align="center" gap="sm" maw={320}>
          <Progress
            value={hasDeterminate ? progressPct : 100}
            size="sm"
            radius="xl"
            w={180}
            animated
            color={progressPct >= 100 && hasDeterminate ? "sage" : "lavender"}
          />
          <Text
            size="lg"
            fw={500}
            ta="center"
            c="var(--mantine-color-text)"
            style={{ letterSpacing: "-0.025em" }}
          >
            {waitStatus.title}
          </Text>
          <Text size="sm" c="dimmed" ta="center" lh={1.55} maw={280}>
            {waitStatus.detail}
          </Text>
          {hasDeterminate && generated > 0 && (
            <Text size="xs" c="dimmed" fw={500}>
              {generated} of {budget} ready
            </Text>
          )}
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
    <Stack key={stem} gap={compact ? 14 : 20} align="stretch" mih={0} style={{ overflow: "hidden", maxHeight: "100%" }}>
      <style>{`
        @keyframes mcq-rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
        .mcq-q { animation: mcq-rise 420ms cubic-bezier(0.32,0.72,0,1) both; }
        .mcq-opt {
          animation: mcq-rise 420ms cubic-bezier(0.32,0.72,0,1) both;
          transition: transform 160ms cubic-bezier(0.32,0.72,0,1), border-color 160ms ease, background 160ms ease, box-shadow 160ms ease;
        }
        .mcq-opt:not(:disabled):hover { transform: translateY(-2px); box-shadow: var(--mantine-shadow-paper); border-color: var(--mantine-color-lavender-4) !important; }
        .mcq-opt:not(:disabled):active { transform: translateY(0); }
        @media (prefers-reduced-motion: reduce) { .mcq-q, .mcq-opt { animation: none !important; } }
      `}</style>

      <Title
        order={2}
        className="mcq-q"
        fw={500}
        lh={1.3}
        ta="center"
        lineClamp={compact ? 4 : 3}
        c="var(--mantine-color-text)"
        style={{
          flexShrink: 0,
          fontFamily: "var(--font-serif), Georgia, serif",
          fontSize: compact ? "clamp(1rem, 4.4vw, 1.3rem)" : "clamp(1.2rem, 2.2vw, 1.9rem)",
          letterSpacing: "-0.01em",
          maxWidth: 640,
          marginInline: "auto",
        }}
      >
        {stem}
      </Title>

      <Stack gap={compact ? 8 : 10} mih={0} style={{ flexShrink: 1, overflow: "auto" }}>
        {safeOptions.map((opt, i) => {
          const value = String(i);
          const isSelected = selected === value;
          const isCorrectOption = graded && gradeState.correctIndex === i;
          const isWrongSelected = graded && !gradeState.correct && isSelected;
          const { border, background, chipBg, chipColor, borderWidth } = mcqOptionChrome(isDark, {
            isSelected,
            isCorrectOption,
            isWrongSelected,
          });
          const dim = optionsLocked && !isCorrectOption && !isWrongSelected;
          return (
            <UnstyledButton
              key={value}
              className="mcq-opt"
              disabled={optionsLocked}
              onClick={() => { if (!optionsLocked) onSelect(value); }}
              style={{
                animationDelay: `${i * 55}ms`,
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

      {feedback && (
        <McqFeedbackCard
          feedback={feedback}
          isCorrect={gradeState?.correct === true}
          compact={compact}
          isDark={isDark}
        />
      )}

      <Stack align="center" gap={8} style={{ flexShrink: 0 }}>
        {showNextQuestion ? (
          <Button
            radius="xl"
            size="md"
            color="sage"
            maw={compact ? "100%" : 300}
            w="100%"
            loading={submitting}
            onClick={onContinue}
            rightSection={<IconArrowRight size={18} stroke={2} />}
          >
            Next question
          </Button>
        ) : (
          <Button
            radius="xl"
            size="md"
            color="lavender"
            maw={compact ? "100%" : 300}
            w="100%"
            onClick={onSubmit}
            loading={submitting}
            disabled={selected === null}
          >
            Check answer
          </Button>
        )}
        {onAdvance && (
          <Button radius="xl" size="sm" variant="subtle" onClick={onAdvance}>
            Next page
          </Button>
        )}
        {!compact && (
          <Text size="xs" c="dimmed" ta="center" style={{ opacity: 0.85 }}>
            {showNextQuestion
              ? "Press Enter for the next question"
              : "Press A–D to choose · Enter to check"}
          </Text>
        )}
      </Stack>
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
  thinking = false,
  contextReady = true,
  onInputChange,
  onSend,
  onStop,
}: {
  messages: { role: string; content: string }[];
  input: string;
  busy: boolean;
  thinking?: boolean;
  contextReady?: boolean;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onStop?: () => void;
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

  useEffect(() => {
    if (messages.length === 0) return;
    scrollToBottom();
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
      <Box
        ref={scrollRef}
        flex={1}
        mih={0}
        pos="relative"
        onScroll={handleScroll}
        style={{ overflow: "auto", overscrollBehavior: "contain" }}
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
                  Questions about this page, the source, or how to think through the answer.
                </Text>
              </Stack>
              <Stack gap={6} w="100%">
                {CHAT_SUGGESTIONS.map((suggestion) => (
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
                  thinking={
                    thinking && m.role === "assistant" && i === messages.length - 1 && !m.content
                  }
                  isDark={isDark}
                />
              ))}
            </Stack>
          </Box>
        )}
        {showJumpLatest ? (
          <Button
            size="compact-xs"
            variant="filled"
            radius="xl"
            pos="absolute"
            bottom={12}
            left="50%"
            style={{ transform: "translateX(-50%)", zIndex: 2 }}
            onClick={() => scrollToBottom(true)}
          >
            Jump to latest
          </Button>
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

function ChatMessage({
  message,
  isUser,
  streaming,
  thinking = false,
  isDark,
}: {
  message: { role: string; content: string };
  isUser: boolean;
  streaming: boolean;
  thinking?: boolean;
  isDark: boolean;
}) {
  if (isUser) {
    return (
      <Box style={{ display: "flex", justifyContent: "flex-end" }}>
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
      </Box>
    );
  }

  return (
    <Group align="flex-start" gap="sm" wrap="nowrap" maw="100%">
      <AssistantLogo size={28} />
      <Box pt={4} style={{ flex: 1, minWidth: 0 }}>
        {thinking || (streaming && !message.content) ? (
          <Group gap="xs" align="center">
            <Loader type="dots" size="sm" />
            <Text size="sm" c="dimmed">
              Thinking…
            </Text>
          </Group>
        ) : (
          <AssistantMarkdown
            content={message.content}
            isDark={isDark}
            streaming={streaming}
          />
        )}
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
    >
      {confirmLabel}
    </Button>
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
                c={hasSelection ? (isDark ? "gray.1" : "dark.8") : "dimmed"}
              >
                {summary}
              </Text>
            </Box>
            <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
              <Button variant="subtle" size="compact-sm" radius="xl" onClick={onSelectAll} px="xs">
                All
              </Button>
              <Button
                variant="subtle"
                size="compact-sm"
                radius="xl"
                onClick={onClearAll}
                disabled={!hasSelection}
                px="xs"
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
      w={SELECTION_DOCK_WIDTH}
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
                  c={hasSelection ? (isDark ? "gray.1" : "dark.8") : "dimmed"}
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
            ) : (
              <Group align="center" wrap="nowrap" gap="lg" w="100%">
                <Box style={{ flex: 1, minWidth: 0 }}>
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

                <Stack gap={2} align="center" style={{ flex: 1.2, minWidth: 0 }}>
                  <Text
                    fw={600}
                    size="sm"
                    ta="center"
                    lineClamp={1}
                    c={hasSelection ? (isDark ? "gray.1" : "dark.8") : "dimmed"}
                  >
                    {summary}
                  </Text>
                  <Text size="xs" c="dimmed" ta="center" lineClamp={1}>
                    Tap a page · Shift+tap to extend
                  </Text>
                </Stack>

                <Group gap="xs" wrap="nowrap" justify="flex-end" style={{ flex: 1, minWidth: 0 }}>
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
  const [renderedForWidth, setRenderedForWidth] = useState(0);
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
  const frameHeight = Math.round(
    renderWidth * (compact ? THUMB_FRAME_ASPECT_COMPACT : THUMB_FRAME_ASPECT),
  );
  const rendered = renderedForWidth === renderWidth && renderWidth > 0;

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
    const coverZoom = compact ? THUMB_COVER_ZOOM_COMPACT : THUMB_COVER_ZOOM;

    void (async () => {
      const canvas = canvasRef.current;
      if (!canvas || cancelled) return;
      try {
        await renderPdfThumbToCanvas(pdfDoc, page, canvas, renderWidth, frameHeight, coverZoom);
        if (!cancelled) setRenderedForWidth(renderWidth);
      } catch {
        if (!cancelled) return;
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [nearViewport, pdfDoc, isPdf, page, renderWidth, frameHeight, compact]);

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
