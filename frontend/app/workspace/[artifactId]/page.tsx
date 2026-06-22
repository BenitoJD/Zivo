"use client";

import { use, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  Alert,
  Badge,
  Box,
  Button,
  Center,
  Grid,
  Group,
  Loader,
  NumberInput,
  Paper,
  Progress,
  Radio,
  RangeSlider,
  ScrollArea,
  SegmentedControl,
  Stack,
  Tabs,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { IconGripVertical, IconPoint } from "@tabler/icons-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { apiFetchBytes, apiGet, apiPost, apiPostSSE, ensureGuestSession, isArtifactId } from "@/lib/api/client";
import { indexingStage } from "@/lib/constants";
import { cancelAllPdfRenders, loadPdfDocument, renderPdfPageToCanvas } from "@/lib/pdf";
import type { ArtifactMeta, AssertionPayload, McqState, PagesInfo } from "@/lib/types";

export default function WorkspaceArtifactPage({
  params,
}: {
  params: Promise<{ artifactId: string }>;
}) {
  const router = useRouter();
  const { artifactId } = use(params);
  const invalidArtifactId = !isArtifactId(artifactId);
  const isLg = useMediaQuery("(min-width: 62em)");
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

  const [queue, setQueue] = useState<McqState | null>(null);
  const [question, setQuestion] = useState("Loading questions…");
  const [options, setOptions] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [mcqLoading, setMcqLoading] = useState(true);

  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<{ role: string; content: string }[]>([]);
  const [chatBusy, setChatBusy] = useState(false);

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
    if (invalidArtifactId || !artifact || selectedRange || !isPdf) return;
    let cancelled = false;
    setPdfLoading(true);
    setPdfError(null);
    setPdfDoc(null);
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
  }, [artifact, artifactId, invalidArtifactId, isPdf, selectedRange]);

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

  useEffect(() => {
    if (!pdfDoc || !isPdf || selectedRange) return;
    let cancelled = false;

    void (async () => {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
      if (cancelled) return;
      for (const p of previewPages) {
        if (cancelled) return;
        const canvas = canvasRefs.current[p];
        if (!canvas) continue;
        try {
          await renderPdfPageToCanvas(pdfDoc, p, canvas);
        } catch {
          if (cancelled) return;
        }
      }
    })();

    return () => {
      cancelled = true;
      cancelAllPdfRenders(Object.values(canvasRefs.current));
    };
  }, [pdfDoc, previewPages, isPdf, selectedRange]);

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
    if (invalidArtifactId || !queue?.current_assertion_id) return;
    apiGet<{ payload: AssertionPayload; title?: string }>(`/api/assertions/${queue.current_assertion_id}`)
      .then((row) => {
        const p = row.payload ?? {};
        setQuestion(p.question ?? p.stem ?? row.title ?? "Question");
        setOptions(p.options ?? p.choices ?? []);
        setSelected(null);
        setFeedback(null);
      })
      .catch(() => {
        setQuestion("Could not load question.");
        setOptions([]);
      });
  }, [queue?.current_assertion_id]);

  async function confirmRange() {
    setConfirming(true);
    setSetupError(null);
    try {
      await apiPost(`/api/artifacts/${artifactId}/page-range`, { from: safeFrom, to: safeTo });
      setArtifact(await apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`));
    } catch (e) {
      setSetupError(e instanceof Error ? e.message : "Could not start indexing");
    } finally {
      setConfirming(false);
    }
  }

  async function submitMcq() {
    if (!queue?.current_assertion_id || selected === null) return;
    const res = await apiPost<{ correct?: boolean; feedback?: string }>("/api/mcq/grade", {
      assertion_id: queue.current_assertion_id,
      choice_index: Number(selected),
    });
    setFeedback(res.feedback ?? (res.correct ? "Correct!" : "Try again."));
    apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`).then(setQueue).catch(() => {});
  }

  async function sendChat() {
    if (!chatInput.trim() || chatBusy) return;
    const userMsg = chatInput.trim();
    setChatInput("");
    setChatBusy(true);
    setChatMessages((m) => [...m, { role: "user", content: userMsg }]);
    try {
      let assistant = "";
      await apiPostSSE("/api/chat", { document_id: artifactId, message: userMsg }, (chunk) => {
        assistant += chunk;
        setChatMessages((m) => {
          const copy = [...m];
          const last = copy[copy.length - 1];
          if (last?.role === "assistant") last.content = assistant;
          else copy.push({ role: "assistant", content: assistant });
          return copy;
        });
      });
    } catch {
      setChatMessages((m) => [
        ...m,
        { role: "assistant", content: "Tutor unavailable — check the API is running and the document is indexed." },
      ]);
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
    return (
      <Stack gap="sm" h="calc(100dvh - 2rem)" mih={0}>
        <Stack gap={2}>
          <Title order={3}>Choose pages to study</Title>
          <Text c="dimmed" size="sm">
            Questions and chat stay scoped to your selection.
          </Text>
        </Stack>
        <Grid flex={1} mih={0} gap="md">
            <Grid.Col
              span={{ base: 12, md: 9 }}
              mih={0}
              styles={{
                col: {
                  minHeight: 0,
                  height: "100%",
                },
              }}
            >
              <Paper withBorder p="sm" h="100%" mih={0}>
                <ScrollArea h="100%" mih={0} offsetScrollbars type="auto">
                  {pdfLoading && (
                    <Center mih={200}>
                      <Loader />
                    </Center>
                  )}
                  {pdfError && (
                    <Text c="red" size="sm" ta="center">
                      {pdfError}
                    </Text>
                  )}
                  {!isPdf && !pdfLoading && (
                    <Text c="dimmed" size="sm" ta="center">
                      Preview is available for PDF sources. Use the range slider to pick pages ({pageCount}{" "}
                      pages).
                    </Text>
                  )}
                  {isPdf && pdfDoc && (
                    <Stack gap="md" pr="sm" align="center">
                      <Text size="xs" c="dimmed" ta="center">
                        Previewing pages {safeFrom}–{safeTo} ({previewPages.length} of {pageCount})
                      </Text>
                      {previewPages.map((p) => (
                        <Paper key={p} withBorder p="xs">
                          <Text size="xs" c="dimmed" mb="xs" ta="center">
                            Page {p}
                          </Text>
                          <Stack align="center">
                            <canvas
                              ref={(el) => {
                                canvasRefs.current[p] = el;
                              }}
                            />
                          </Stack>
                        </Paper>
                      ))}
                    </Stack>
                  )}
                </ScrollArea>
              </Paper>
            </Grid.Col>
            <Grid.Col
              span={{ base: 12, md: 3 }}
              styles={{
                col: {
                  alignSelf: "flex-start",
                },
              }}
            >
              <Paper
                withBorder
                p="md"
                pos="sticky"
                top="md"
                bg="var(--mantine-color-body)"
                styles={{ root: { zIndex: 2 } }}
              >
                <Stack gap="md">
                  <Text size="sm" fw={500}>
                    Page range · {safeFrom}–{safeTo} of {pageCount}
                  </Text>
                  <Box pb="xs">
                    <RangeSlider
                      mt="sm"
                      mb="lg"
                      min={1}
                      max={pageCount}
                      minRange={1}
                      step={1}
                      value={[safeFrom, safeTo]}
                      onChange={([from, to]) => {
                        setPageFrom(from);
                        setPageTo(to);
                      }}
                      marks={sliderMarks}
                      label={(v) => `Page ${v}`}
                      thumbSize={26}
                      thumbChildren={<IconGripVertical size={20} stroke={1.5} />}
                      thumbFromLabel="Start page"
                      thumbToLabel="End page"
                      thumbValueText={(v) => `Page ${v}`}
                      restrictToMarks={pageCount <= 12}
                      styles={{
                        thumb: { borderWidth: 2, padding: 3 },
                      }}
                    />
                  </Box>
                  <Group grow>
                    <NumberInput
                      label="From"
                      min={1}
                      max={pageCount}
                      value={safeFrom}
                      onChange={(v) => setPageFrom(typeof v === "number" ? v : 1)}
                    />
                    <NumberInput
                      label="To"
                      min={safeFrom}
                      max={pageCount}
                      value={safeTo}
                      onChange={(v) => setPageTo(typeof v === "number" ? v : safeFrom)}
                    />
                  </Group>
                  <Button fullWidth variant="white" c="dark.9" onClick={() => void confirmRange()} loading={confirming}>
                    Confirm range
                  </Button>
                  {setupError && (
                    <Text size="sm" c="red">
                      {setupError}
                    </Text>
                  )}
                </Stack>
              </Paper>
            </Grid.Col>
          </Grid>
        </Stack>
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
    const showWorkerHint = progress < 20;

    return (
      <Center mih="calc(100dvh - 2rem)">
        <Paper withBorder p="xl" maw={520} w="100%">
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
            {showWorkerHint && (
              <Alert variant="light" color="gray" title="Taking longer than expected?">
                Indexing runs in the background. If progress stays at {progress}% for more than a minute, start
                local workers with{" "}
                <Text span ff="monospace" size="sm">
                  ./scripts/dev.sh start
                </Text>
                .
              </Alert>
            )}
          </Stack>
        </Paper>
      </Center>
    );
  }

  return (
    <Stack gap="md" h="calc(100dvh - 2rem)">
      <SegmentedControl
        value={mode}
        onChange={(v) => setMode(v as "learn" | "test")}
        data={[
          { label: "Learn", value: "learn" },
          { label: "Test", value: "test" },
        ]}
      />

      {mode === "test" && (
        <Paper p="md" withBorder h="100%">
          <Stack gap="md">
            <Title order={4}>{stem}</Title>
            <Radio.Group value={selected} onChange={setSelected}>
              <Stack gap="xs">
                {options.map((opt, i) => (
                  <Radio
                    key={i}
                    value={String(i)}
                    label={
                      <Text size="sm">
                        <Text span fw={600} mr="xs">
                          {i + 1}.
                        </Text>
                        {opt}
                      </Text>
                    }
                  />
                ))}
              </Stack>
            </Radio.Group>
            {feedback && (
              <Alert variant="light" color={feedback.includes("Correct") ? "green" : "gray"}>
                {feedback}
              </Alert>
            )}
            <Button onClick={() => void submitMcq()} disabled={selected === null}>
              Submit
            </Button>
          </Stack>
        </Paper>
      )}

      {mode === "learn" && isLg && (
        <Grid flex={1} mih={0} gap="md">
          <Grid.Col span={6}>
            <Paper p="md" withBorder h="100%">
              <Stack gap="md">
                {queue && (
                  <Group gap="xs">
                    <Badge variant="light">Concepts: {queue.concepts.length}</Badge>
                    <Badge variant="light" color={queue.page_mastered ? "green" : "gray"}>
                      Page mastered: {queue.page_mastered ? "yes" : "no"}
                    </Badge>
                  </Group>
                )}
                <Title order={4}>{stem}</Title>
                <Radio.Group value={selected} onChange={setSelected}>
                  <Stack gap="xs">
                    {options.map((opt, i) => (
                      <Radio
                        key={i}
                        value={String(i)}
                        label={
                          <Text size="sm">
                            <Text span fw={600} mr="xs">
                              {i + 1}.
                            </Text>
                            {opt}
                          </Text>
                        }
                      />
                    ))}
                  </Stack>
                </Radio.Group>
                {feedback && (
                  <Alert variant="light" color={feedback.includes("Correct") ? "green" : "gray"}>
                    {feedback}
                  </Alert>
                )}
                <Group>
                  <Button onClick={() => void submitMcq()} disabled={selected === null}>
                    Submit
                  </Button>
                  {queue?.page_mastered && !queue.page_ready && (
                    <Button
                      variant="light"
                      onClick={() =>
                        apiPost(`/api/artifacts/${artifactId}/pages/1/advance`, {}).then(() =>
                          apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`).then(setQueue),
                        )
                      }
                    >
                      I&apos;m ready for the next page
                    </Button>
                  )}
                </Group>
              </Stack>
            </Paper>
          </Grid.Col>
          <Grid.Col span={6}>
            <Stack h="100%" gap="md">
              <Paper p="md" withBorder flex={1}>
                <Title order={5} mb="sm">
                  Source
                </Title>
                <Text size="sm" c="dimmed">
                  Artifact {artifactId} — PDF / code viewer mounts here.
                </Text>
              </Paper>
              <Paper p="md" withBorder flex={1}>
                <Stack h="100%" gap="sm">
                  <Title order={5}>Tutor</Title>
                  <ScrollArea flex={1} offsetScrollbars>
                    <Stack gap="sm" pb="sm">
                      {chatMessages.length === 0 && (
                        <Text size="sm" c="dimmed">
                          Ask about the current page or question.
                        </Text>
                      )}
                      {chatMessages.map((m, i) => (
                        <Paper key={i} p="sm" radius="md" bg={m.role === "user" ? "gray.8" : "dark.6"}>
                          <Text size="sm">{m.content}</Text>
                        </Paper>
                      ))}
                    </Stack>
                  </ScrollArea>
                  <Group align="flex-end" wrap="nowrap" gap="xs">
                    <Textarea
                      flex={1}
                      placeholder="Ask about the current question…"
                      value={chatInput}
                      onChange={(e) => setChatInput(e.currentTarget.value)}
                      disabled={chatBusy}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          void sendChat();
                        }
                      }}
                      autosize
                      minRows={1}
                      maxRows={4}
                    />
                    <Button onClick={() => void sendChat()} disabled={chatBusy || !chatInput.trim()}>
                      Send
                    </Button>
                  </Group>
                </Stack>
              </Paper>
            </Stack>
          </Grid.Col>
        </Grid>
      )}

      {mode === "learn" && !isLg && (
        <Tabs defaultValue="mcq" flex={1} mih={0}>
          <Tabs.List grow>
            <Tabs.Tab value="mcq">MCQ</Tabs.Tab>
            <Tabs.Tab value="source">Source</Tabs.Tab>
            <Tabs.Tab value="chat">Chat</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="mcq" pt="md">
            <Paper p="md" withBorder>
              <Stack gap="md">
                {queue && (
                  <Group gap="xs">
                    <Badge variant="light">Concepts: {queue.concepts.length}</Badge>
                    <Badge variant="light" color={queue.page_mastered ? "green" : "gray"}>
                      Page mastered: {queue.page_mastered ? "yes" : "no"}
                    </Badge>
                  </Group>
                )}
                <Title order={4}>{stem}</Title>
                <Radio.Group value={selected} onChange={setSelected}>
                  <Stack gap="xs">
                    {options.map((opt, i) => (
                      <Radio
                        key={i}
                        value={String(i)}
                        label={
                          <Text size="sm">
                            <Text span fw={600} mr="xs">
                              {i + 1}.
                            </Text>
                            {opt}
                          </Text>
                        }
                      />
                    ))}
                  </Stack>
                </Radio.Group>
                {feedback && (
                  <Alert variant="light" color={feedback.includes("Correct") ? "green" : "gray"}>
                    {feedback}
                  </Alert>
                )}
                <Group>
                  <Button onClick={() => void submitMcq()} disabled={selected === null}>
                    Submit
                  </Button>
                  {queue?.page_mastered && !queue.page_ready && (
                    <Button
                      variant="light"
                      onClick={() =>
                        apiPost(`/api/artifacts/${artifactId}/pages/1/advance`, {}).then(() =>
                          apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`).then(setQueue),
                        )
                      }
                    >
                      I&apos;m ready for the next page
                    </Button>
                  )}
                </Group>
              </Stack>
            </Paper>
          </Tabs.Panel>
          <Tabs.Panel value="source" pt="md">
            <Paper p="md" withBorder>
              <Title order={5} mb="sm">
                Source
              </Title>
              <Text size="sm" c="dimmed">
                Artifact {artifactId} — PDF / code viewer mounts here.
              </Text>
            </Paper>
          </Tabs.Panel>
          <Tabs.Panel value="chat" pt="md">
            <Paper p="md" withBorder>
              <Stack gap="sm">
                <Title order={5}>Tutor</Title>
                <ScrollArea h={280} offsetScrollbars>
                  <Stack gap="sm" pb="sm">
                    {chatMessages.length === 0 && (
                      <Text size="sm" c="dimmed">
                        Ask about the current page or question.
                      </Text>
                    )}
                    {chatMessages.map((m, i) => (
                      <Paper key={i} p="sm" radius="md" bg={m.role === "user" ? "gray.8" : "dark.6"}>
                        <Text size="sm">{m.content}</Text>
                      </Paper>
                    ))}
                  </Stack>
                </ScrollArea>
                <Group align="flex-end" wrap="nowrap" gap="xs">
                  <Textarea
                    flex={1}
                    placeholder="Ask about the current question…"
                    value={chatInput}
                    onChange={(e) => setChatInput(e.currentTarget.value)}
                    disabled={chatBusy}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        void sendChat();
                      }
                    }}
                    autosize
                    minRows={1}
                    maxRows={4}
                  />
                  <Button onClick={() => void sendChat()} disabled={chatBusy || !chatInput.trim()}>
                    Send
                  </Button>
                </Group>
              </Stack>
            </Paper>
          </Tabs.Panel>
        </Tabs>
      )}
    </Stack>
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
