"use client";

import {
  use,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type WheelEvent,
} from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
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
  IconArrowUp,
  IconArrowsMaximize,
  IconCheck,
  IconClipboardList,
  IconFileText,
  IconGripVertical,
  IconMessageCircle,
  IconPoint,
  IconX,
  IconZoomIn,
  IconZoomOut,
} from "@tabler/icons-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { apiFetchBytes, apiGet, apiPost, apiPostSSE, ensureGuestSession, humanizeApiFailure, isArtifactId } from "@/lib/api/client";
import { BRAND_LOGO_HEIGHT, BRAND_LOGO_SRC, BRAND_LOGO_WIDTH, ZIVO_ASSISTANT_NAME } from "@/lib/brand";
import { AssistantMarkdown } from "@/lib/chatMarkdown";
import { indexingStage } from "@/lib/constants";
import { learnWaitStatus } from "@/lib/learnStatus";
import {
  cancelAllPdfRenders,
  clampPdfScroll,
  loadPdfDocument,
  pdfDisplayHeight,
  pdfPageAspectRatio,
  pdfPageFitScale,
  renderPdfPageToCanvas,
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
const THUMB_GAP = 20;
const THUMB_MIN_WIDTH = 188;
const THUMB_MIN_WIDTH_COMPACT = 132;
const SELECTION_PAD_X = 20;
const SELECTION_PAD_Y = 12;

function shellBleedPx(compact: boolean): number {
  return compact ? 10 : 16;
}

function computeGridLayout(gridWidth: number, minThumbWidth: number, gap: number) {
  if (gridWidth < 1) return { cols: 2, thumbWidth: minThumbWidth };
  const cols = Math.max(1, Math.floor((gridWidth + gap) / (minThumbWidth + gap)));
  const totalGap = gap * Math.max(0, cols - 1);
  const thumbWidth = Math.floor((gridWidth - totalGap) / cols);
  return { cols, thumbWidth };
}

export default function WorkspaceArtifactPage({
  params,
}: {
  params: Promise<{ artifactId: string }>;
}) {
  const router = useRouter();
  const { artifactId } = use(params);
  const invalidArtifactId = !isArtifactId(artifactId);
  const isLg = useMediaQuery(STUDY_DESKTOP_BP);
  const isCompact = useMediaQuery(STUDY_COMPACT_BP);
  const useOverlayRails = useMediaQuery(STUDY_OVERLAY_BP);
  const mounted = useMounted();
  const { colorScheme } = useMantineColorScheme();
  const isDark = mounted ? colorScheme === "dark" : true;
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
  const apiPageCount = pages?.page_count ?? artifact?.meta?.page_count ?? 1;
  const pageCount = Math.max(apiPageCount, pdfDoc?.numPages ?? 0, 1);
  const sortedSelection = useMemo(
    () => [...selectedPages].filter((p) => p >= 1 && p <= pageCount).sort((a, b) => a - b),
    [selectedPages, pageCount],
  );
  const sliderFrom = sortedSelection[0] ?? 1;
  const sliderTo =
    sortedSelection.length > 0
      ? sortedSelection[sortedSelection.length - 1]
      : Math.min(5, pageCount);
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

  useEffect(() => {
    if (invalidArtifactId) return;
    void ensureGuestSession();
    let cancelled = false;
    Promise.all([
      apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`),
      apiGet<PagesInfo>(`/api/artifacts/${artifactId}/pages`),
    ])
      .then(([art, pg]) => {
        if (!cancelled) {
          setArtifact(art);
          setPages(pg);
          const count = pg.page_count || art.meta?.page_count || 1;
          setSelectedPages(defaultInitialPages(count));
          setLastClickedPage(1);
        }
      })
      .catch((e) => {
        if (!cancelled) setSetupError(e instanceof Error ? e.message : "Could not load source");
      });
    return () => {
      cancelled = true;
    };
  }, [artifactId, invalidArtifactId]);

  useEffect(() => {
    if (invalidArtifactId || artifact?.status !== "indexing") return;
    let cancelled = false;
    const poll = () => {
      apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`)
        .then((art) => {
          if (!cancelled) setArtifact(art);
        })
        .catch(() => {});
    };
    poll();
    const id = window.setInterval(poll, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [artifact?.status, artifactId, invalidArtifactId]);

  useEffect(() => {
    if (invalidArtifactId) return;
    let cancelled = false;
    void (async () => {
      try {
        await ensureGuestSession();
        const history = await apiGet<{ role: string; content: string }[]>(
          `/api/chat/threads/${artifactId}/messages`,
        );
        if (!cancelled) {
          setChatMessages(
            history
              .filter((m) => (m.content || "").trim())
              .map((m) => ({ role: m.role, content: m.content })),
          );
        }
      } catch {
        /* thread may not exist yet */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [artifactId, invalidArtifactId]);

  const isPdf = artifact?.content_type === "application/pdf";

  useEffect(() => {
    setPdfDoc(null);
    setPdfError(null);
    setPdfLoading(false);
  }, [artifactId]);

  useEffect(() => {
    if (invalidArtifactId || !artifact || !isPdf) return;
    let cancelled = false;
    setPdfLoading(true);
    setPdfError(null);
    void (async () => {
      try {
        const buf = await apiFetchBytes(`/api/documents/${artifactId}/file`);
        if (cancelled) return;
        const pdf = await loadPdfDocument(buf);
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
    setSelectedPages((prev) => {
      const filtered = prev.filter((p) => p <= n);
      return filtered.length > 0 ? filtered : defaultInitialPages(n);
    });
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
    if (invalidArtifactId || !selectedRange || artifact?.status === "indexing") return;
    let cancelled = false;
    apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`)
      .then((data) => {
        if (!cancelled) setQueue(data);
      })
      .catch(() => {
        if (!cancelled) setQuestion("Sign in or reload to load questions.");
      })
      .finally(() => {
        if (!cancelled) setMcqLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [artifactId, invalidArtifactId, selectedRange, artifact?.status]);

  useEffect(() => {
    if (invalidArtifactId || !selectedRange || artifact?.status !== "ready") return;
    if (queue?.current_assertion_id) return;
    let cancelled = false;
    const poll = () => {
      apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`)
        .then((data) => {
          if (!cancelled) setQueue(data);
        })
        .catch(() => {});
    };
    poll();
    const id = window.setInterval(poll, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [artifactId, invalidArtifactId, selectedRange, artifact?.status, queue?.current_assertion_id]);

  useEffect(() => {
    if (invalidArtifactId || !queue?.current_assertion_id) return;
    apiGet<{ payload: AssertionPayload; title?: string }>(`/api/assertions/${queue.current_assertion_id}`)
      .then((row) => {
        const p = row.payload ?? {};
        setQuestion(sanitizeMcqStem(p.question ?? p.stem ?? row.title ?? "Question"));
        setOptions(normalizeMcqOptions(p.options, p.choices));
        setQuestionSequence(typeof p.sequence === "number" ? p.sequence : null);
        setSelected(null);
        setFeedback(null);
        setGradeState(null);
      })
      .catch(() => {
        setQuestion("Could not load question.");
        setOptions([]);
        setQuestionSequence(null);
      });
  }, [queue?.current_assertion_id, invalidArtifactId]);

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
      await apiPostSSE(
        "/api/chat",
        {
          document_id: artifactId,
          message: userMsg,
          scope,
        },
        (chunk) => {
          gotToken = true;
          assistant += chunk;
          setChatMessages((m) => {
            const copy = [...m];
            const last = copy[copy.length - 1];
            if (last?.role === "assistant") last.content = assistant;
            else copy.push({ role: "assistant", content: assistant });
            return copy;
          });
        },
      );
      if (!gotToken || !assistant.trim()) {
        throw new Error("empty response");
      }
    } catch (e) {
      const raw = e instanceof Error && e.message ? e.message : null;
      const timedOut = raw && /timed out|aborted/i.test(raw);
      const detail = timedOut
        ? `${ZIVO_ASSISTANT_NAME} is still waking up — try again in a moment.`
        : raw && raw !== "empty response"
          ? humanizeApiFailure(0, raw)
          : `${ZIVO_ASSISTANT_NAME} could not reply right now. Try again in a moment.`;
      setChatMessages((m) => {
        const copy = [...m];
        const last = copy[copy.length - 1];
        if (last?.role === "assistant") {
          last.content = detail;
        } else {
          copy.push({ role: "assistant", content: detail });
        }
        return copy;
      });
    } finally {
      setChatBusy(false);
    }
  }

  if (invalidArtifactId) {
    return (
      <Center mih="50vh">
        <Loader />
      </Center>
    );
  }

  if (setupError && !artifact) {
    return (
      <Center mih="50vh">
        <Stack align="center" gap="md" maw={420}>
          <Alert color="red" title="Could not load source" variant="light">
            {setupError}
          </Alert>
          <Button variant="white" c="dark.9" onClick={() => router.push("/workspace")}>
            Back to library
          </Button>
        </Stack>
      </Center>
    );
  }

  if (!artifact || !pages) {
    return (
      <Center mih="50vh">
        <Loader />
      </Center>
    );
  }

  if (!selectedRange) {
    const shortName = artifact.filename?.replace(/\.[^.]+$/, "") ?? "Source";

    return (
      <PageSelectionScreen
        title="Choose pages to study"
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
            <Title order={3}>Indexing failed</Title>
            <Text c="dimmed" ta="center">
              We could not finish preparing pages {selectedRange?.from}–{selectedRange?.to}. Try a smaller
              range or upload the file again.
            </Text>
            <Button variant="white" c="dark.9" onClick={() => window.location.reload()}>
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
              <Text size="lg" fw={500} ta="center" style={{ letterSpacing: "-0.02em" }}>
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
              <Progress value={progress} size="md" radius="xl" animated={progress < 100} />
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
                color="blue"
                onClick={openSource}
              />
            )}
            {!tutorOpen && (
              <StudyEdgeTrigger
                side="right"
                icon={<IconMessageCircle size={20} stroke={2} />}
                label={ZIVO_ASSISTANT_NAME}
                color="grape"
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
          <ThemeIcon size={52} radius="xl" variant="light" color="green">
            <IconClipboardList size={26} stroke={1.5} />
          </ThemeIcon>
          <Title
            order={2}
            ta="center"
            fw={600}
            style={{ letterSpacing: "-0.04em", lineHeight: 1.15 }}
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
  const progressLabel =
    showProgress && questionTotal > 0 ? `Question ${questionIndex} of ${questionTotal}` : null;

  return (
    <Group
      px={compact ? "sm" : { base: "sm", sm: "md", lg: "lg" }}
      py={compact ? 6 : 8}
      justify="space-between"
      align="center"
      wrap="nowrap"
      gap="xs"
      style={{ flexShrink: 0 }}
    >
      <Text size="xs" c="dimmed" fw={500} ff="monospace" style={{ minWidth: compact ? 40 : 48 }}>
        {progressLabel ?? ""}
      </Text>
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
                  background: selected ? "var(--mantine-color-blue-light)" : "transparent",
                }}
              >
                <Stack gap={2} align="center">
                  <Icon
                    size={22}
                    stroke={selected ? 2.25 : 1.75}
                    color={selected ? "var(--mantine-color-blue-filled)" : "var(--mantine-color-dimmed)"}
                  />
                  <Text size="10px" fw={selected ? 700 : 500} c={selected ? "blue" : "dimmed"} lh={1.1}>
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
        <Loader size="sm" />
      </Center>
    );
  }

  if (pdfError) {
    return (
      <Center flex={1} px="md">
        <Text c="red" size="sm" ta="center">
          {pdfError}
        </Text>
      </Center>
    );
  }

  const pageSurface = isDark ? "white" : "white";
  const pageGap = displayPages.length > 1 ? 8 : 0;
  const activePage = displayPages[0];

  return (
    <Stack gap={0} h="100%" mih={0}>
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
          }}
        >
          <Stack gap={pageGap} align="center">
            {pdfDoc &&
              displayPages.map((p) => {
                const aspect = pageAspects[p] ?? 0;
                const displayHeight =
                  pageDisplayWidth > 0 && aspect > 0 ? pdfDisplayHeight(pageDisplayWidth, aspect) : undefined;
                const isActivePage = p === activePage;
                return (
                  <Paper
                    key={p}
                    data-pdf-page
                    ref={(el) => {
                      pageRefs.current[p] = el;
                    }}
                    withBorder
                    shadow="sm"
                    radius="sm"
                    bg={pageSurface}
                    pos="relative"
                    style={{
                      width: pageDisplayWidth > 0 ? pageDisplayWidth : "100%",
                      maxWidth: "100%",
                      lineHeight: 0,
                      outline: isActivePage ? "3px solid var(--mantine-color-blue-filled)" : undefined,
                      outlineOffset: 2,
                      overflow: "hidden",
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
                  </Paper>
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
    </Stack>
  );
}

function PdfReaderToolbar({
  zoom,
  zoomMin,
  zoomMax,
  onZoomIn,
  onZoomOut,
  onFitWidth,
  isDark,
  canPan = false,
}: {
  zoom: number;
  zoomMin: number;
  zoomMax: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitWidth: () => void;
  isDark: boolean;
  canPan?: boolean;
}) {
  const atFit = Math.abs(zoom - 1) < 0.01;
  const hint = canPan ? "Drag · scroll · ⌘/Ctrl+zoom" : "Scroll · ⌘/Ctrl+zoom";

  return (
    <Box
      px="xs"
      py={4}
      style={{
        flexShrink: 0,
        borderTop: "1px solid var(--mantine-color-default-border)",
        background: isDark ? "var(--mantine-color-dark-7)" : "var(--mantine-color-white)",
      }}
    >
      <Group justify="space-between" wrap="nowrap" gap={4}>
        <Tooltip label="Fit page width">
          <Button
            variant={atFit ? "filled" : "light"}
            color="blue"
            size="compact-xs"
            radius="md"
            leftSection={<IconArrowsMaximize size={13} />}
            onClick={onFitWidth}
            px="xs"
          >
            Fit width
          </Button>
        </Tooltip>
        <Group gap={2} wrap="nowrap">
          <Tooltip label="Zoom out">
            <ActionIcon
              variant="light"
              color="blue"
              size="sm"
              radius="md"
              onClick={onZoomOut}
              disabled={zoom <= zoomMin}
              aria-label="Zoom out"
            >
              <IconZoomOut size={16} stroke={2} />
            </ActionIcon>
          </Tooltip>
          <Text size="10px" fw={600} w={40} ta="center">
            {Math.round(zoom * 100)}%
          </Text>
          <Tooltip label="Zoom in">
            <ActionIcon
              variant="light"
              color="blue"
              size="sm"
              radius="md"
              onClick={onZoomIn}
              disabled={zoom >= zoomMax}
              aria-label="Zoom in"
            >
              <IconZoomIn size={16} stroke={2} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
      <Text size="9px" c="dimmed" ta="center" mt={2} lh={1.2}>
        {hint}
      </Text>
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
        <ThemeIcon size={compact || inDrawer ? 52 : 72} radius="xl" variant="light" color="gray">
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
      ? "rgba(64, 192, 87, 0.07)"
      : "rgba(64, 192, 87, 0.05)"
    : isDark
      ? "rgba(255, 255, 255, 0.03)"
      : "rgba(0, 0, 0, 0.02)";
  const outline = isCorrect
    ? isDark
      ? "rgba(64, 192, 87, 0.18)"
      : "rgba(64, 192, 87, 0.22)"
    : isDark
      ? "var(--mantine-color-dark-5)"
      : "var(--mantine-color-gray-2)";
  const primaryText = isDark ? "var(--mantine-color-gray-1)" : "var(--mantine-color-dark-7)";
  const secondaryText = isDark ? "var(--mantine-color-gray-4)" : "var(--mantine-color-gray-7)";

  return (
    <Box
      style={{
        flexShrink: 0,
        textAlign: "left",
        padding: compact ? "14px 16px" : "16px 18px",
        borderRadius: 12,
        background: surface,
        border: `1px solid ${outline}`,
        maxHeight: compact ? 148 : 184,
        overflow: "auto",
      }}
    >
      <Text
        size={compact ? "sm" : "md"}
        lh={1.7}
        c={primaryText}
        style={{ whiteSpace: "pre-wrap", fontSize: compact ? undefined : "1.0625rem" }}
      >
        <Text component="span" fw={600} inherit>
          {isCorrect ? "Exactly — " : "Not quite — "}
        </Text>
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
    stem.toLowerCase().includes("loading") ||
    stem.toLowerCase().includes("indexing") ||
    stem.toLowerCase().includes("will appear") ||
    (Boolean(queue?.generation_pending) && !queue?.current_assertion_id) ||
    (!queue?.current_assertion_id &&
      artifactStatus === "ready" &&
      safeOptions.length === 0 &&
      (queue?.generation_pending || (queue?.questions_generated ?? 0) === 0));

  const [statusTick, setStatusTick] = useState(0);
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
  }, 3200);
  useEffect(() => {
    setStatusTick(0);
  }, [waitStatus.rotateKey]);

  if (waiting) {
    return (
      <Center py={compact ? "lg" : "xl"}>
        <Stack align="center" gap="sm" maw={320}>
          <Loader type="oval" size="sm" />
          <Text
            size="lg"
            fw={500}
            ta="center"
            c={isDark ? "gray.2" : "dark.6"}
            style={{ letterSpacing: "-0.025em" }}
          >
            {waitStatus.title}
          </Text>
          <Text size="sm" c="dimmed" ta="center" lh={1.55} maw={280}>
            {waitStatus.detail}
          </Text>
        </Stack>
      </Center>
    );
  }

  return (
    <Stack gap={compact ? 12 : 16} align="stretch" mih={0} style={{ overflow: "hidden", maxHeight: "100%" }}>
      <Title
        order={2}
        fw={600}
        lh={1.3}
        ta="center"
        lineClamp={compact ? 4 : 3}
        style={{
          flexShrink: 0,
          fontSize: compact ? "clamp(0.95rem, 4.2vw, 1.25rem)" : "clamp(1rem, 2vw, 1.5rem)",
          letterSpacing: "-0.02em",
        }}
      >
        {stem}
      </Title>

      <Stack gap={compact ? 6 : 8} mih={0} style={{ flexShrink: 1, overflow: "hidden" }}>
        {safeOptions.map((opt, i) => {
          const value = String(i);
          const isSelected = selected === value;
          const isCorrectOption = graded && gradeState.correctIndex === i;
          const isWrongSelected = graded && !gradeState.correct && isSelected;
          let borderColor = "var(--mantine-color-default-border)";
          let background = "transparent";
          if (isCorrectOption) {
            borderColor = "var(--mantine-color-green-filled)";
            background = isDark ? "rgba(64, 192, 87, 0.16)" : "rgba(64, 192, 87, 0.1)";
          } else if (isWrongSelected) {
            borderColor = "var(--mantine-color-red-filled)";
            background = isDark ? "rgba(250, 82, 82, 0.14)" : "rgba(250, 82, 82, 0.08)";
          } else if (isSelected) {
            borderColor = "var(--mantine-color-blue-filled)";
            background = isDark ? "rgba(51, 154, 240, 0.14)" : "rgba(51, 154, 240, 0.08)";
          }
          return (
            <UnstyledButton
              key={value}
              onClick={() => {
                if (optionsLocked) return;
                onSelect(value);
              }}
              disabled={optionsLocked}
              style={{
                width: "100%",
                textAlign: "left",
                borderRadius: 12,
                padding: compact ? "14px 12px" : "12px 14px",
                minHeight: 44,
                border: isSelected || isCorrectOption || isWrongSelected ? `2px solid ${borderColor}` : `1px solid ${borderColor}`,
                background,
                opacity: optionsLocked && !isCorrectOption && !isWrongSelected ? 0.65 : 1,
                transition: "border-color 120ms ease, background 120ms ease",
                cursor: optionsLocked ? "default" : "pointer",
              }}
            >
              <Group wrap="nowrap" align="flex-start" gap="sm">
                <Text size="sm" c="dimmed" w={20} ta="center" ff="monospace" fw={600}>
                  {String.fromCharCode(65 + i)}
                </Text>
                <Text size={compact ? "sm" : "md"} lh={1.5} style={{ flex: 1, fontSize: compact ? undefined : "1.0625rem" }}>
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

      <Stack align="center" gap="xs" style={{ flexShrink: 0 }}>
        {showNextQuestion ? (
          <Button
            radius="xl"
            size={compact ? "md" : "md"}
            color="green"
            maw={compact ? "100%" : 280}
            w="100%"
            loading={submitting}
            onClick={onContinue}
          >
            Next question
          </Button>
        ) : (
          <Button
            radius="xl"
            size="md"
            color="blue"
            maw={compact ? "100%" : 280}
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
      </Stack>
    </Stack>
  );
}

function AssistantLogo({ size }: { size: number }) {
  const height = size;
  const width = Math.round((size * BRAND_LOGO_WIDTH) / BRAND_LOGO_HEIGHT);
  return (
    <Image
      src={BRAND_LOGO_SRC}
      alt={ZIVO_ASSISTANT_NAME}
      width={width}
      height={height}
      unoptimized
      style={{
        width,
        height,
        flexShrink: 0,
        objectFit: "contain",
        display: "block",
      }}
    />
  );
}

function TutorPanel({
  messages,
  input,
  busy,
  contextReady = true,
  onInputChange,
  onSend,
}: {
  messages: { role: string; content: string }[];
  input: string;
  busy: boolean;
  contextReady?: boolean;
  onInputChange: (value: string) => void;
  onSend: () => void;
}) {
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  const scrollRef = useRef<HTMLDivElement>(null);
  const canSend = Boolean(input.trim()) && !busy && contextReady;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || messages.length === 0) return;
    requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
  }, [messages, busy]);

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
                  isDark={isDark}
                />
              ))}
            </Stack>
          </Box>
        )}
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
  isDark,
}: {
  message: { role: string; content: string };
  isUser: boolean;
  streaming: boolean;
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
        {streaming && !message.content ? (
          <Loader type="dots" size="sm" />
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

function defaultInitialPages(count: number): number[] {
  return pagesInRange(1, Math.min(5, count));
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
  title,
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
  title: string;
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
      style={{
        display: "flex",
        flexDirection: "column",
        margin: -bleed,
        width: `calc(100% + ${bleed * 2}px)`,
        height: `calc(100% + ${bleed * 2}px)`,
        boxSizing: "border-box",
      }}
    >
      <Group
        justify="space-between"
        align="flex-end"
        wrap="nowrap"
        gap="lg"
        px={SELECTION_PAD_X}
        py={SELECTION_PAD_Y}
        style={{ flexShrink: 0, borderBottom: "1px solid var(--mantine-color-default-border)" }}
      >
        <Stack gap={2} style={{ minWidth: 0 }}>
          <Title order={isCompact ? 4 : 3} style={{ letterSpacing: "-0.04em" }} lineClamp={1}>
            {title}
          </Title>
          <Text size="sm" c="dimmed" lineClamp={1}>
            {subtitle}
          </Text>
        </Stack>
        <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
          {pdfLoading && <Loader size="xs" />}
          <Text size="sm" fw={600}>
            {formatSelectionSummary(selectedPages, pageCount)}
          </Text>
        </Group>
      </Group>

      {pdfError && (
        <Text c="red" size="sm" px="md" pt="xs">
          {pdfError}
        </Text>
      )}
      {!isPdf && !pdfLoading && (
        <Text c="dimmed" size="sm" px="md" pt="xs">
          PDF thumbnails load automatically. Use the slider below ({pageCount} pages).
        </Text>
      )}

      <Box
        flex={1}
        mih={0}
        px={SELECTION_PAD_X}
        py={SELECTION_PAD_Y}
        style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}
      >
        <PageSelectionBody
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
  const sliderColor = isDark ? "blue.4" : "blue.6";
  const thumbBg = isDark ? "var(--mantine-color-dark-6)" : "var(--mantine-color-white)";
  const thumbBorder = isDark ? "var(--mantine-color-blue-4)" : "var(--mantine-color-blue-7)";
  const trackBg = isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)";

  return (
    <Stack gap="md" flex={1} mih={0} h="100%" style={{ minHeight: 0, overflow: "hidden" }}>
      <Box style={{ flexShrink: 0, paddingInline: 14 }}>
        <PageRangeQuickSelect
          sliderFrom={sliderFrom}
          sliderTo={sliderTo}
          pageCount={pageCount}
          sliderMarks={sliderMarks}
          isDark={isDark}
          sliderColor={sliderColor}
          thumbBg={thumbBg}
          thumbBorder={thumbBorder}
          trackBg={trackBg}
          onRangeChange={onRangeChange}
        />
      </Box>
      <PageThumbnailGrid
        pageCount={pageCount}
        selectedPages={selectedPages}
        isDark={isDark}
        isPdf={isPdf}
        pdfDoc={pdfDoc}
        thumbCanvasRefs={thumbCanvasRefs}
        onPageToggle={onPageToggle}
      />
      <Box
        py={8}
        style={{
          flexShrink: 0,
          borderTop: "1px solid var(--mantine-color-default-border)",
        }}
      >
        <PageSelectionControls
          selectedPages={selectedPages}
          pageCount={pageCount}
          confirming={confirming}
          setupError={setupError}
          confirmLabel={confirmLabel}
          onSelectAll={onSelectAll}
          onClearAll={onClearAll}
          onConfirm={onConfirm}
        />
      </Box>
    </Stack>
  );
}

function PageRangeQuickSelect({
  sliderFrom,
  sliderTo,
  pageCount,
  sliderMarks,
  isDark,
  sliderColor,
  thumbBg,
  thumbBorder,
  trackBg,
  onRangeChange,
}: {
  sliderFrom: number;
  sliderTo: number;
  pageCount: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  isDark: boolean;
  sliderColor: string;
  thumbBg: string;
  thumbBorder: string;
  trackBg: string;
  onRangeChange: (from: number, to: number) => void;
}) {
  return (
    <Stack gap="md">
      <RangeSlider
        color={sliderColor}
        min={1}
        max={pageCount}
        minRange={1}
        step={1}
        value={[sliderFrom, sliderTo]}
        onChange={([from, to]) => onRangeChange(from, to)}
        marks={sliderMarks}
        label={(v) => `Page ${v}`}
        thumbSize={24}
        thumbChildren={<IconGripVertical size={14} stroke={1.5} />}
        thumbFromLabel="Start page"
        thumbToLabel="End page"
        thumbValueText={(v) => `Page ${v}`}
        restrictToMarks={pageCount <= 12}
        mb={4}
        styles={{
          root: { paddingTop: 8, paddingBottom: 32, overflow: "visible" },
          track: { backgroundColor: trackBg },
          bar: {
            backgroundColor: isDark ? "var(--mantine-color-blue-4)" : "var(--mantine-color-blue-6)",
          },
          thumb: {
            backgroundColor: thumbBg,
            borderColor: thumbBorder,
            borderWidth: 2,
            color: thumbBorder,
          },
          mark: {
            borderColor: isDark ? "var(--mantine-color-dark-3)" : "var(--mantine-color-gray-5)",
          },
          markLabel: {
            color: "var(--mantine-color-dimmed)",
            marginTop: 10,
          },
        }}
      />
      <Group grow gap="md">
        <NumberInput
          label="From"
          size="sm"
          min={1}
          max={pageCount}
          value={sliderFrom}
          onChange={(v) => onRangeChange(typeof v === "number" ? v : 1, sliderTo)}
        />
        <NumberInput
          label="To"
          size="sm"
          min={sliderFrom}
          max={pageCount}
          value={sliderTo}
          onChange={(v) => onRangeChange(sliderFrom, typeof v === "number" ? v : sliderFrom)}
        />
      </Group>
    </Stack>
  );
}

function PageSelectionControls({
  selectedPages,
  confirming,
  setupError,
  confirmLabel,
  onSelectAll,
  onClearAll,
  onConfirm,
}: {
  selectedPages: number[];
  pageCount: number;
  confirming: boolean;
  setupError: string | null;
  confirmLabel: string;
  onSelectAll: () => void;
  onClearAll: () => void;
  onConfirm: () => void;
}) {
  return (
    <Stack gap={4} w="100%" maw="100%">
      <Group justify="space-between" align="center" wrap="nowrap" gap="sm" w="100%" maw="100%">
        <Text size="xs" c="dimmed" lineClamp={1} visibleFrom="sm" style={{ flex: 1, minWidth: 0 }}>
          Slider for range · tap to toggle · Shift+tap to extend
        </Text>
        <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
          <Button variant="subtle" size="compact-xs" onClick={onSelectAll}>
            All
          </Button>
          <Button variant="subtle" size="compact-xs" onClick={onClearAll}>
            Clear
          </Button>
          <Button
            size="compact-sm"
            variant="filled"
            color="blue"
            radius="md"
            onClick={onConfirm}
            loading={confirming}
            disabled={selectedPages.length === 0}
          >
            {confirmLabel}
          </Button>
        </Group>
      </Group>
      {setupError && (
        <Text size="xs" c="red">
          {setupError}
        </Text>
      )}
    </Stack>
  );
}

function PageThumbnailGrid({
  pageCount,
  selectedPages,
  isDark,
  isPdf,
  pdfDoc,
  thumbCanvasRefs,
  onPageToggle,
}: {
  pageCount: number;
  selectedPages: number[];
  isDark: boolean;
  isPdf: boolean;
  pdfDoc: PDFDocumentProxy | null;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  onPageToggle: (page: number, shiftKey: boolean) => void;
}) {
  const selectedSet = useMemo(() => new Set(selectedPages), [selectedPages]);
  const gridRef = useRef<HTMLDivElement>(null);
  const [gridWidth, setGridWidth] = useState(0);

  // On narrow (phone) grids, use a smaller thumb floor so we get 2 columns
  // instead of a single oversized column that forces excessive scrolling.
  const thumbMinWidth = gridWidth > 0 && gridWidth < 420 ? THUMB_MIN_WIDTH_COMPACT : THUMB_MIN_WIDTH;
  const { cols, thumbWidth } = computeGridLayout(gridWidth, thumbMinWidth, THUMB_GAP);

  useEffect(() => {
    const el = gridRef.current;
    if (!el) return;
    const update = () => setGridWidth(el.clientWidth);
    update();
    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!pdfDoc || !isPdf || thumbWidth < 1) return;
    let cancelled = false;
    const renderScale = Math.min(0.5, Math.max(0.22, thumbWidth / 320));

    void (async () => {
      for (let p = 1; p <= pageCount; p += 1) {
        if (cancelled) return;
        const canvas = thumbCanvasRefs.current[p];
        if (!canvas) continue;
        try {
          await renderPdfPageToCanvas(pdfDoc, p, canvas, renderScale, thumbWidth);
        } catch {
          if (cancelled) return;
        }
      }
    })();

    return () => {
      cancelled = true;
      cancelAllPdfRenders(Object.values(thumbCanvasRefs.current));
    };
  }, [pdfDoc, isPdf, pageCount, thumbCanvasRefs, thumbWidth]);

  const pageNumbers = useMemo(
    () => Array.from({ length: pageCount }, (_, index) => index + 1),
    [pageCount],
  );

  return (
    <Box ref={gridRef} flex={1} mih={0} h="100%" w="100%" style={{ overflow: "hidden" }}>
      <ScrollArea h="100%" flex={1} mih={0} w="100%" offsetScrollbars type="auto" scrollbarSize={8} pt={4}>
        <Box
          w="100%"
          pb={SELECTION_PAD_Y}
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
            gap: THUMB_GAP,
          }}
        >
          {pageNumbers.map((page) => (
            <PageThumbnailCell
              key={page}
              page={page}
              selected={selectedSet.has(page)}
              isDark={isDark}
              isPdf={isPdf}
              thumbWidth={thumbWidth}
              thumbCanvasRefs={thumbCanvasRefs}
              onToggle={onPageToggle}
            />
          ))}
        </Box>
      </ScrollArea>
    </Box>
  );
}

function PageThumbnailCell({
  page,
  selected,
  isDark,
  isPdf,
  thumbWidth,
  thumbCanvasRefs,
  onToggle,
}: {
  page: number;
  selected: boolean;
  isDark: boolean;
  isPdf: boolean;
  thumbWidth: number;
  thumbCanvasRefs: React.MutableRefObject<Record<number, HTMLCanvasElement | null>>;
  onToggle: (page: number, shiftKey: boolean) => void;
}) {
  const placeholderHeight = Math.round(thumbWidth * 1.35);
  const ringColor = isDark ? "var(--mantine-color-blue-4)" : "var(--mantine-color-blue-6)";
  const idleRing = isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-4)";

  return (
    <UnstyledButton
      onClick={(event) => onToggle(page, event.shiftKey)}
      aria-label={`Page ${page}${selected ? ", selected" : ""}`}
      aria-pressed={selected}
      w="100%"
    >
      <Box pos="relative" w="100%">
        <Box
          bg={isDark ? "dark.7" : "gray.1"}
          p={2}
          style={{
            borderRadius: 10,
            lineHeight: 0,
            boxShadow: selected
              ? `0 0 0 2px ${ringColor}, 0 8px 20px rgba(0, 0, 0, 0.22)`
              : `0 0 0 1px ${idleRing}`,
            opacity: selected ? 1 : 0.88,
            transition: "box-shadow 160ms ease, opacity 160ms ease",
          }}
        >
          {isPdf ? (
            <canvas
              ref={(el) => {
                thumbCanvasRefs.current[page] = el;
              }}
              style={{
                width: "100%",
                height: "auto",
                display: "block",
                borderRadius: 8,
                verticalAlign: "top",
              }}
            />
          ) : (
            <Center h={placeholderHeight} w="100%">
              <IconFileText size={32} stroke={1.25} color="var(--mantine-color-dimmed)" />
            </Center>
          )}
        </Box>
        <Text
          size="sm"
          ta="center"
          mt={10}
          fw={selected ? 700 : 500}
          c={selected ? "blue" : "dimmed"}
          lh={1}
        >
          {page}
        </Text>
        {selected && (
          <ThemeIcon
            pos="absolute"
            top={8}
            right={8}
            size={24}
            radius="xl"
            color="blue"
            variant="filled"
            style={{ boxShadow: "0 2px 8px rgba(0,0,0,0.3)" }}
          >
            <IconCheck size={14} stroke={3} />
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
