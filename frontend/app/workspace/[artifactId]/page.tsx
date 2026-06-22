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
import { useDisclosure, useLocalStorage, useMediaQuery, useMounted } from "@mantine/hooks";
import {
  IconArrowUp,
  IconArrowsMaximize,
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
import { apiFetchBytes, apiGet, apiPost, apiPostSSE, ensureGuestSession, isArtifactId } from "@/lib/api/client";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";
import { indexingStage } from "@/lib/constants";
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
import type { ArtifactMeta, AssertionPayload, McqGradeResponse, McqState, PagesInfo } from "@/lib/types";

const PAGE_RANGE_PANEL_WIDTH = 300;
const SOURCE_PANEL_DEFAULT = 360;
const SOURCE_PANEL_MIN = 280;
const SOURCE_PANEL_MAX = 720;
const TUTOR_PANEL_DEFAULT = 400;
const TUTOR_PANEL_MIN = 300;
const TUTOR_PANEL_MAX = 560;
const STUDY_CENTER_MIN = 380;
const PANEL_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";
const PANEL_MS = 280;
const STUDY_DESKTOP_BP = "(min-width: 62em)";
const STUDY_COMPACT_BP = "(max-width: 47.99em)";

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
  const mounted = useMounted();
  const { colorScheme } = useMantineColorScheme();
  const isDark = mounted ? colorScheme === "dark" : true;
  const [mode, setMode] = useState<"learn" | "test">("learn");

  const [artifact, setArtifact] = useState<ArtifactMeta | null>(null);
  const [pages, setPages] = useState<PagesInfo | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [pageFrom, setPageFrom] = useState(1);
  const [pageTo, setPageTo] = useState(1);
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [pdfLoading, setPdfLoading] = useState(false);
  const canvasRefs = useRef<Record<number, HTMLCanvasElement | null>>({});
  const pdfViewerRef = useRef<HTMLDivElement>(null);
  const [viewerWidth, setViewerWidth] = useState(0);
  const [panelPos, setPanelPos] = useState<{ x: number; y: number } | null>(null);

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
  const advanceTimerRef = useRef<number | null>(null);

  const selectedRange = artifact?.meta?.selected_range;
  const apiPageCount = pages?.page_count ?? artifact?.meta?.page_count ?? 1;
  const pageCount = Math.max(apiPageCount, pdfDoc?.numPages ?? 0, 1);
  const safeTo = Math.min(pageTo, pageCount);
  const safeFrom = Math.min(pageFrom, safeTo);
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
          setPageTo(Math.min(5, count));
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
    setPageFrom((from) => Math.min(from, n));
    setPageTo((to) => (to <= 1 ? Math.min(5, n) : Math.min(to, n)));
  }, [pdfDoc]);

  const previewPages = useMemo(() => {
    const pages: number[] = [];
    for (let p = safeFrom; p <= safeTo; p += 1) pages.push(p);
    return pages;
  }, [safeFrom, safeTo]);

  const studyPages = useMemo(() => {
    if (!selectedRange) return [];
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
    if (!pdfDoc || !isPdf || selectedRange) return;
    let cancelled = false;

    void (async () => {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
      if (cancelled || viewerWidth < 1) return;
      for (const p of previewPages) {
        if (cancelled) return;
        const canvas = canvasRefs.current[p];
        if (!canvas) continue;
        try {
          const scale = await pdfPageFitScale(pdfDoc, p, viewerWidth);
          if (cancelled) return;
          await renderPdfPageToCanvas(pdfDoc, p, canvas, scale);
        } catch {
          if (cancelled) return;
        }
      }
    })();

    return () => {
      cancelled = true;
      cancelAllPdfRenders(Object.values(canvasRefs.current));
    };
  }, [pdfDoc, previewPages, isPdf, selectedRange, viewerWidth]);

  useEffect(() => {
    if (selectedRange) return;
    const el = pdfViewerRef.current;
    if (!el) return;
    const update = () => setViewerWidth(el.clientWidth);
    update();
    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => ro.disconnect();
  }, [selectedRange, artifact?.id]);

  useEffect(() => {
    if (selectedRange || panelPos !== null || isCompact) return;
    const x = Math.max(16, window.innerWidth - PAGE_RANGE_PANEL_WIDTH - 24);
    setPanelPos({ x, y: 72 });
  }, [panelPos, selectedRange, isCompact]);

  useEffect(() => {
    if (!panelPos) return;
    const onResize = () => {
      setPanelPos((pos) => (pos ? clampPanelPosition(pos, null) : pos));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [panelPos]);

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
        setQuestion(p.question ?? p.stem ?? row.title ?? "Question");
        setOptions(p.options ?? p.choices ?? []);
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
    clearAdvanceTimer();
  }, [mode]);

  useEffect(() => {
    if (!queue?.page_complete || queue.current_assertion_id || queue.document_complete) return;
    const id = window.setTimeout(() => {
      void refreshQueue();
    }, 1500);
    return () => window.clearTimeout(id);
  }, [queue?.page_complete, queue?.current_assertion_id, queue?.document_complete]);

  function clearAdvanceTimer() {
    if (advanceTimerRef.current !== null) {
      window.clearTimeout(advanceTimerRef.current);
      advanceTimerRef.current = null;
    }
  }

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
    clearAdvanceTimer();
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

      if (mode === "test") {
        await advanceMcq();
      } else {
        advanceTimerRef.current = window.setTimeout(() => void advanceMcq(), 1400);
      }
    } catch {
      setFeedback("Could not grade answer — try again.");
    } finally {
      setSubmitting(false);
    }
  }

  async function confirmRange() {
    setConfirming(true);
    setSetupError(null);
    try {
      await apiPost(`/api/artifacts/${artifactId}/page-range`, { from: safeFrom, to: safeTo });
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
    setPageFrom(suggestion.from);
    setPageTo(suggestion.to);
    setSetupError(null);
    setReselectOpen(true);
  }

  async function sendChat() {
    if (!chatInput.trim() || chatBusy) return;
    const userMsg = chatInput.trim();
    setChatInput("");
    setChatBusy(true);
    setChatMessages((m) => [...m, { role: "user", content: userMsg }, { role: "assistant", content: "" }]);
    try {
      await ensureGuestSession();
      const currentPage = queue?.current_page;
      const scope =
        currentPage && currentPage > 0
          ? {
              current_page: currentPage,
              page_start: Math.max(1, currentPage - 1),
              page_end: pageCount ? Math.min(pageCount, currentPage + 1) : currentPage + 1,
            }
          : {};
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
    } catch {
      setChatMessages((m) => {
        const copy = [...m];
        const last = copy[copy.length - 1];
        const fallback = `${ZIVO_ASSISTANT_NAME} could not reply right now. Try again in a moment.`;
        if (last?.role === "assistant") {
          last.content = fallback;
        } else {
          copy.push({ role: "assistant", content: fallback });
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
    const sliderColor = isDark ? "blue.4" : "blue.6";
    const thumbBg = isDark ? "var(--mantine-color-dark-6)" : "var(--mantine-color-white)";
    const thumbBorder = isDark ? "var(--mantine-color-blue-4)" : "var(--mantine-color-blue-7)";
    const trackBg = isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)";

    return (
      <Box
        flex={1}
        mih={0}
        h="100%"
        style={{ overflow: "hidden", display: "flex", flexDirection: "column" }}
      >
        <ScrollArea flex={1} mih={0} offsetScrollbars type="auto">
          <Box ref={pdfViewerRef} w="100%">
            {pdfLoading && (
              <Center mih="40vh">
                <Loader />
              </Center>
            )}
            {pdfError && (
              <Text c="red" size="sm" ta="center" p="md">
                {pdfError}
              </Text>
            )}
            {!isPdf && !pdfLoading && (
              <Center mih="40vh">
                <Text c="dimmed" size="sm" ta="center" maw={420} px="md">
                  Preview is available for PDF sources. Use the page range panel to pick pages ({pageCount}{" "}
                  pages).
                </Text>
              </Center>
            )}
            {isPdf && pdfDoc && (
              <Stack gap={0}>
                {previewPages.map((p) => (
                  <Box key={p} w="100%">
                    <canvas
                      ref={(el) => {
                        canvasRefs.current[p] = el;
                      }}
                      style={{ width: "100%", height: "auto", display: "block" }}
                    />
                  </Box>
                ))}
              </Stack>
            )}
          </Box>
        </ScrollArea>

        {isCompact ? (
          <PageRangeBottomSheet>
            <PageRangeControls
              safeFrom={safeFrom}
              safeTo={safeTo}
              pageCount={pageCount}
              previewCount={previewPages.length}
              sliderMarks={sliderMarks}
              isDark={isDark}
              sliderColor={sliderColor}
              thumbBg={thumbBg}
              thumbBorder={thumbBorder}
              trackBg={trackBg}
              confirming={confirming}
              setupError={setupError}
              onRangeChange={(from, to) => {
                setPageFrom(from);
                setPageTo(to);
              }}
              onFromChange={setPageFrom}
              onToChange={setPageTo}
              onConfirm={() => void confirmRange()}
            />
          </PageRangeBottomSheet>
        ) : (
          panelPos && (
            <DraggablePageRangePanel position={panelPos} onPositionChange={setPanelPos}>
              <PageRangeControls
                safeFrom={safeFrom}
                safeTo={safeTo}
                pageCount={pageCount}
                previewCount={previewPages.length}
                sliderMarks={sliderMarks}
                isDark={isDark}
                sliderColor={sliderColor}
                thumbBg={thumbBg}
                thumbBorder={thumbBorder}
                trackBg={trackBg}
                confirming={confirming}
                setupError={setupError}
                onRangeChange={(from, to) => {
                  setPageFrom(from);
                  setPageTo(to);
                }}
                onFromChange={setPageFrom}
                onToChange={setPageTo}
                onConfirm={() => void confirmRange()}
              />
            </DraggablePageRangePanel>
          )
        )}
      </Box>
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
              <Loader type="bars" />
              <Title order={3} ta="center">
                {stage.title}
              </Title>
              <Text c="dimmed" ta="center" size="sm">
                {stage.detail}
              </Text>
              <Text size="sm" c="dimmed">
                Pages {selectedRange.from}–{selectedRange.to}
                {artifact.filename ? ` · ${artifact.filename}` : ""}
              </Text>
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
  const completedRange = selectedRange;
  const nextRangeSuggestion = completedRange
    ? suggestNextPageRange(completedRange, pageCount)
    : null;

  const questionColumn = (
    <Box flex={1} mih={0} h="100%" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <StudyMetaBar
        currentPage={queue?.current_page}
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
                onInputChange={setChatInput}
                onSend={() => void sendChat()}
              />
            </StudyPushRail>
          </Box>
        </>
      ) : (
        <StudyMobileShell
          question={questionColumn}
          source={
            <StudySourcePanel
              filename={artifact.filename}
              pageRange={selectedRange}
              currentPage={queue?.current_page}
              isPdf={isPdf}
              pdfLoading={pdfLoading}
              pdfError={pdfError}
              pdfDoc={pdfDoc}
              studyPages={studyPages}
              open
            />
          }
          tutor={
            <TutorPanel
              messages={chatMessages}
              input={chatInput}
              busy={chatBusy}
              onInputChange={setChatInput}
              onSend={() => void sendChat()}
            />
          }
        />
      )}
      {reselectOpen && (
        <StudyRangeReselectOverlay
          filename={shortFilename}
          pageCount={pageCount}
          completedRange={completedRange}
          safeFrom={safeFrom}
          safeTo={safeTo}
          previewCount={previewPages.length}
          sliderMarks={sliderMarks}
          isDark={isDark}
          isCompact={isCompact}
          confirming={confirming}
          setupError={setupError}
          bookFinished={nextRangeSuggestion?.bookFinished ?? false}
          onRangeChange={(from, to) => {
            setPageFrom(from);
            setPageTo(to);
          }}
          onFromChange={setPageFrom}
          onToChange={setPageTo}
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
  safeFrom,
  safeTo,
  previewCount,
  sliderMarks,
  isDark,
  isCompact,
  confirming,
  setupError,
  bookFinished,
  onRangeChange,
  onFromChange,
  onToChange,
  onClose,
  onConfirm,
}: {
  filename: string;
  pageCount: number;
  completedRange?: { from: number; to: number };
  safeFrom: number;
  safeTo: number;
  previewCount: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  isDark: boolean;
  isCompact: boolean;
  confirming: boolean;
  setupError: string | null;
  bookFinished: boolean;
  onRangeChange: (from: number, to: number) => void;
  onFromChange: (value: number) => void;
  onToChange: (value: number) => void;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const sliderColor = isDark ? "blue.4" : "blue.6";
  const thumbBg = isDark ? "var(--mantine-color-dark-6)" : "var(--mantine-color-white)";
  const thumbBorder = isDark ? "var(--mantine-color-blue-4)" : "var(--mantine-color-blue-7)";
  const trackBg = isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)";
  const completedLabel =
    completedRange && completedRange.from === completedRange.to
      ? `page ${completedRange.from}`
      : completedRange
        ? `pages ${completedRange.from}–${completedRange.to}`
        : "your last range";

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
        p="lg"
        w="100%"
        maw={isCompact ? "100%" : 440}
        onClick={(e) => e.stopPropagation()}
        style={{
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
                ? `You finished ${completedLabel}. Select any ${pageCount}-page range to continue.`
                : `You finished ${completedLabel}. Choose how many pages to study next.`}
            </Text>
          </Stack>
          <ActionIcon variant="subtle" color="gray" onClick={onClose} aria-label="Close">
            <IconX size={18} />
          </ActionIcon>
        </Group>
        <PageRangeControls
          safeFrom={safeFrom}
          safeTo={safeTo}
          pageCount={pageCount}
          previewCount={previewCount}
          sliderMarks={sliderMarks}
          isDark={isDark}
          sliderColor={sliderColor}
          thumbBg={thumbBg}
          thumbBorder={thumbBorder}
          trackBg={trackBg}
          confirming={confirming}
          setupError={setupError}
          confirmLabel="Start studying"
          onRangeChange={onRangeChange}
          onFromChange={onFromChange}
          onToChange={onToChange}
          onConfirm={onConfirm}
        />
      </Paper>
    </Box>
  );
}

function PageCompleteInterstitial({
  page,
  compact,
  generating,
}: {
  page: number;
  compact?: boolean;
  generating?: boolean;
}) {
  return (
    <Center py={compact ? "md" : "xl"}>
      <Stack align="center" gap="md" maw={400}>
        <Loader type="dots" size="sm" />
        <Title order={3} ta="center" fw={600} style={{ letterSpacing: "-0.03em" }}>
          Page {page} complete
        </Title>
        <Text size="sm" c="dimmed" ta="center" lh={1.55}>
          {generating
            ? "Preparing questions for the next page…"
            : "Moving to the next page…"}
        </Text>
      </Stack>
    </Center>
  );
}

function StudyMetaBar({
  currentPage,
  questionIndex,
  questionTotal,
  mode,
  onModeChange,
  showProgress = true,
  compact = false,
}: {
  currentPage?: number;
  questionIndex: number;
  questionTotal: number;
  mode: "learn" | "test";
  onModeChange: (mode: "learn" | "test") => void;
  showProgress?: boolean;
  compact?: boolean;
}) {
  const progressLabel =
    showProgress && questionTotal > 0
      ? currentPage
        ? `Page ${currentPage} · Question ${questionIndex} of ${questionTotal}`
        : `Question ${questionIndex} of ${questionTotal}`
      : null;

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
  source,
  tutor,
}: {
  question: ReactNode;
  source: ReactNode;
  tutor: ReactNode;
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
        <Box
          hidden={active !== "question"}
          h="100%"
          style={{ display: active === "question" ? "flex" : "none", flexDirection: "column", overflow: "hidden" }}
        >
          {question}
        </Box>
        <Box
          hidden={active !== "source"}
          h="100%"
          style={{ display: active === "source" ? "flex" : "none", flexDirection: "column", overflow: "hidden" }}
        >
          {source}
        </Box>
        <Box
          hidden={active !== "tutor"}
          h="100%"
          style={{ display: active === "tutor" ? "flex" : "none", flexDirection: "column", overflow: "hidden" }}
        >
          {tutor}
        </Box>
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
  isResizing?: boolean;
  onResizeStart?: () => void;
  onResizeEnd?: () => void;
  onWidthChange?: (width: number) => void;
}) {
  const railBorder = "1px solid var(--mantine-color-default-border)";

  return (
    <Box
      pos="relative"
      h="100%"
      style={{
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
      }}
    >
      <Box
        w={width}
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
      {open && resizable && onWidthChange && (
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

  const zoomMin = PDF_ZOOM_PRESETS[0];
  const zoomMax = PDF_ZOOM_PRESETS[PDF_ZOOM_PRESETS.length - 1];
  const canPan = zoom > 1.01;

  useEffect(() => {
    if (!open) setZoom(1);
  }, [open]);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el || !open) return;
    const update = () => setViewerWidth(Math.max(1, el.clientWidth));
    update();
    const ro = new ResizeObserver(() => update());
    ro.observe(el);
    return () => ro.disconnect();
  }, [open]);

  useEffect(() => {
    if (!pdfDoc || !open) return;
    let cancelled = false;
    void (async () => {
      const aspects: Record<number, number> = {};
      for (const p of studyPages) {
        if (cancelled) return;
        aspects[p] = await pdfPageAspectRatio(pdfDoc, p);
      }
      if (!cancelled) setPageAspects(aspects);
    })();
    return () => {
      cancelled = true;
    };
  }, [pdfDoc, studyPages, open]);

  const pageDisplayWidth = viewerWidth > 0 ? Math.round(viewerWidth * zoom) : 0;

  useEffect(() => {
    if (!pdfDoc || !isPdf || !open || pageDisplayWidth < 1) return;
    let cancelled = false;

    void (async () => {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
      if (cancelled) return;
      for (const p of studyPages) {
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
  }, [pdfDoc, studyPages, isPdf, open, viewerWidth, zoom, pageDisplayWidth]);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    clampPdfScroll(el);
  }, [pageDisplayWidth, studyPages, pageAspects]);

  useEffect(() => {
    if (!open || !currentPage) return;
    const id = window.setTimeout(() => {
      pageRefs.current[currentPage]?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
      const el = viewportRef.current;
      if (el) clampPdfScroll(el);
    }, 120);
    return () => window.clearTimeout(id);
  }, [open, currentPage, studyPages, pageDisplayWidth]);

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
  const pageGap = studyPages.length > 1 ? 8 : 0;

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
              studyPages.map((p) => {
                const aspect = pageAspects[p] ?? 0;
                const displayHeight =
                  pageDisplayWidth > 0 && aspect > 0 ? pdfDisplayHeight(pageDisplayWidth, aspect) : undefined;
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
                      outline: currentPage === p ? "3px solid var(--mantine-color-blue-filled)" : undefined,
                      outlineOffset: 2,
                      overflow: "hidden",
                    }}
                  >
                    {currentPage === p && (
                      <Box
                        pos="absolute"
                        top={0}
                        left={0}
                        right={0}
                        style={{ zIndex: 2, lineHeight: 1.4 }}
                      >
                        <Text
                          size="xs"
                          fw={600}
                          c="blue"
                          px="sm"
                          py={4}
                          bg="rgba(255,255,255,0.92)"
                          style={{ borderBottom: "1px solid var(--mantine-color-gray-2)" }}
                        >
                          Studying this page
                        </Text>
                      </Box>
                    )}
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
          {isCorrect ? "Got it. " : "Not quite. "}
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
  const graded = gradeState !== null;
  const showNextQuestion = mode === "learn" && graded;
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
      options.length === 0 &&
      (queue?.generation_pending || (queue?.generated_on_page ?? 0) === 0));

  if (waiting) {
    const indexing = artifactStatus === "indexing";
    const page = queue?.current_page;
    const generating = Boolean(queue?.generation_pending);
    const planning = !indexing && !generating && (queue?.questions_generated ?? 0) === 0;
    const rangeLabel =
      queue?.page_from && queue?.page_to
        ? `Studying pages ${queue.page_from}–${queue.page_to}`
        : null;
    return (
      <Stack align="center" gap="lg" py="md">
        <Loader type="dots" size="sm" />
        <Stack gap={6} align="center" maw={440}>
          <Title order={3} ta="center" fw={600} style={{ letterSpacing: "-0.03em" }}>
            {indexing
              ? "Reading your source"
              : generating
                ? "Writing your questions"
                : planning
                  ? "Planning this page"
                  : "Almost ready"}
          </Title>
          {page && !indexing && (
            <Text size="sm" c="dimmed" ta="center">
              {rangeLabel ? `${rangeLabel} · now on page ${page}` : `Page ${page}`}
            </Text>
          )}
          <Text size="sm" c="dimmed" ta="center" lh={1.5}>
            {indexing
              ? "Zivo is indexing the pages you selected so questions stay scoped to what matters."
              : generating
                ? `${ZIVO_ASSISTANT_NAME} is writing questions for this page — a few at a time, as you go.`
                : planning
                  ? "Figuring out what this page can test, then writing your first questions."
                  : "Finishing up — your first question will appear in a moment."}
          </Text>
        </Stack>
      </Stack>
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
        {options.map((opt, i) => {
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
            disabled={selected === null || (graded && mode === "learn" && gradeState?.correct)}
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

function TutorPanel({
  messages,
  input,
  busy,
  onInputChange,
  onSend,
}: {
  messages: { role: string; content: string }[];
  input: string;
  busy: boolean;
  onInputChange: (value: string) => void;
  onSend: () => void;
}) {
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  const scrollRef = useRef<HTMLDivElement>(null);
  const canSend = Boolean(input.trim()) && !busy;

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
              <ThemeIcon size={36} radius="xl" variant="filled" color={isDark ? "gray" : "dark"}>
                <Text size="xs" fw={700} c={isDark ? "dark.8" : "white"}>
                  Z
                </Text>
              </ThemeIcon>
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
              placeholder={`Message ${ZIVO_ASSISTANT_NAME}`}
              value={input}
              onChange={(e) => onInputChange(e.currentTarget.value)}
              disabled={busy}
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
      <ThemeIcon size={28} radius="xl" variant="filled" color={isDark ? "gray" : "dark"} style={{ flexShrink: 0 }}>
        <Text size="10px" fw={700} c={isDark ? "dark.8" : "white"} tt="uppercase">
          Z
        </Text>
      </ThemeIcon>
      <Box pt={4} style={{ flex: 1, minWidth: 0 }}>
        {streaming && !message.content ? (
          <Loader type="dots" size="sm" />
        ) : (
          <Text size="sm" lh={1.7} style={{ whiteSpace: "pre-wrap" }}>
            {message.content}
            {streaming && message.content ? (
              <Text span inherit c="dimmed">
                {" "}
                ▍
              </Text>
            ) : null}
          </Text>
        )}
      </Box>
    </Group>
  );
}

function clampPanel(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function clampPanelPosition(
  pos: { x: number; y: number },
  panelEl: HTMLDivElement | null,
): { x: number; y: number } {
  const w = panelEl?.offsetWidth ?? PAGE_RANGE_PANEL_WIDTH;
  const h = panelEl?.offsetHeight ?? 280;
  const edge = 8;
  return {
    x: clampPanel(pos.x, edge, Math.max(edge, window.innerWidth - w - edge)),
    y: clampPanel(pos.y, edge, Math.max(edge, window.innerHeight - h - edge)),
  };
}

function PageRangeBottomSheet({ children }: { children: React.ReactNode }) {
  return (
    <Paper
      pos="fixed"
      left={0}
      right={0}
      bottom={0}
      w="100%"
      shadow="xl"
      withBorder
      p="md"
      radius={0}
      bg="var(--mantine-color-body)"
      style={{
        zIndex: 200,
        borderTopLeftRadius: 16,
        borderTopRightRadius: 16,
        maxHeight: "min(52vh, 420px)",
        overflow: "auto",
        paddingBottom: "max(12px, env(safe-area-inset-bottom))",
      }}
    >
      <Box mb="sm">
        <Text size="sm" fw={600}>
          Page range
        </Text>
        <Text size="xs" c="dimmed">
          Choose which pages to study
        </Text>
      </Box>
      {children}
    </Paper>
  );
}

function PageRangeControls({
  safeFrom,
  safeTo,
  pageCount,
  previewCount,
  sliderMarks,
  isDark,
  sliderColor,
  thumbBg,
  thumbBorder,
  trackBg,
  confirming,
  setupError,
  confirmLabel = "Confirm range",
  onRangeChange,
  onFromChange,
  onToChange,
  onConfirm,
}: {
  safeFrom: number;
  safeTo: number;
  pageCount: number;
  previewCount: number;
  sliderMarks: { value: number; label?: ReactNode }[];
  isDark: boolean;
  sliderColor: string;
  thumbBg: string;
  thumbBorder: string;
  trackBg: string;
  confirming: boolean;
  setupError: string | null;
  confirmLabel?: string;
  onRangeChange: (from: number, to: number) => void;
  onFromChange: (value: number) => void;
  onToChange: (value: number) => void;
  onConfirm: () => void;
}) {
  return (
    <Stack gap="md">
      <Text size="xs" c="dimmed">
        {safeFrom}–{safeTo} of {pageCount} pages · previewing {previewCount}
      </Text>
      <RangeSlider
        color={sliderColor}
        min={1}
        max={pageCount}
        minRange={1}
        step={1}
        value={[safeFrom, safeTo]}
        onChange={([from, to]) => onRangeChange(from, to)}
        marks={sliderMarks}
        label={(v) => `Page ${v}`}
        thumbSize={24}
        thumbChildren={<IconGripVertical size={14} stroke={1.5} />}
        thumbFromLabel="Start page"
        thumbToLabel="End page"
        thumbValueText={(v) => `Page ${v}`}
        restrictToMarks={pageCount <= 12}
        styles={{
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
          },
        }}
      />
      <Group grow>
        <NumberInput
          label="From"
          size="xs"
          min={1}
          max={pageCount}
          value={safeFrom}
          onChange={(v) => onFromChange(typeof v === "number" ? v : 1)}
        />
        <NumberInput
          label="To"
          size="xs"
          min={safeFrom}
          max={pageCount}
          value={safeTo}
          onChange={(v) => onToChange(typeof v === "number" ? v : safeFrom)}
        />
      </Group>
      <Button fullWidth size="md" variant="filled" color="blue" onClick={onConfirm} loading={confirming}>
        {confirmLabel}
      </Button>
      {setupError && (
        <Text size="xs" c="red">
          {setupError}
        </Text>
      )}
    </Stack>
  );
}

function DraggablePageRangePanel({
  children,
  position,
  onPositionChange,
}: {
  children: React.ReactNode;
  position: { x: number; y: number };
  onPositionChange: (pos: { x: number; y: number }) => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const dragState = useRef<{
    pointerX: number;
    pointerY: number;
    originX: number;
    originY: number;
  } | null>(null);

  function onHandlePointerDown(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    dragState.current = {
      pointerX: e.clientX,
      pointerY: e.clientY,
      originX: position.x,
      originY: position.y,
    };
  }

  function onHandlePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragState.current) return;
    const dx = e.clientX - dragState.current.pointerX;
    const dy = e.clientY - dragState.current.pointerY;
    onPositionChange(
      clampPanelPosition(
        {
          x: dragState.current.originX + dx,
          y: dragState.current.originY + dy,
        },
        panelRef.current,
      ),
    );
  }

  function endDrag(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragState.current) return;
    dragState.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
  }

  return (
    <Paper
      ref={panelRef}
      pos="fixed"
      w={PAGE_RANGE_PANEL_WIDTH}
      shadow="xl"
      withBorder
      p="md"
      bg="var(--mantine-color-body)"
      style={{
        left: position.x,
        top: position.y,
        zIndex: 200,
      }}
    >
      <Box
        mb="sm"
        py={4}
        style={{ cursor: "grab", touchAction: "none" }}
        onPointerDown={onHandlePointerDown}
        onPointerMove={onHandlePointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        aria-label="Drag page range panel"
      >
        <Group gap="xs" wrap="nowrap">
          <IconGripVertical size={16} stroke={1.5} />
          <Text size="sm" fw={600} style={{ flex: 1 }}>
            Page range
          </Text>
          <Text size="xs" c="dimmed">
            drag
          </Text>
        </Group>
      </Box>
      {children}
    </Paper>
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
