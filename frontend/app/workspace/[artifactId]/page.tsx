"use client";

import { use, useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Center,
  Grid,
  Group,
  Loader,
  NumberInput,
  Paper,
  Radio,
  ScrollArea,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Tabs,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { apiGet, apiPost, apiPostSSE, ensureGuestSession } from "@/lib/api/client";
import type { ArtifactMeta, AssertionPayload, McqState, PagesInfo } from "@/lib/types";

export default function WorkspaceArtifactPage({
  params,
}: {
  params: Promise<{ artifactId: string }>;
}) {
  const { artifactId } = use(params);
  const isLg = useMediaQuery("(min-width: 62em)");
  const [mode, setMode] = useState<"learn" | "test">("learn");

  const [artifact, setArtifact] = useState<ArtifactMeta | null>(null);
  const [pages, setPages] = useState<PagesInfo | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [pageFrom, setPageFrom] = useState(1);
  const [pageTo, setPageTo] = useState(1);

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
  const pageCount = pages?.page_count ?? artifact?.meta?.page_count ?? 1;
  const safeTo = Math.min(pageTo, pageCount || 1);
  const safeFrom = Math.min(pageFrom, safeTo);

  const stem =
    mcqLoading
      ? "Loading questions…"
      : queue && !queue.current_assertion_id
        ? "Questions will appear once indexing finishes."
        : question;

  useEffect(() => {
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
          setPageTo(Math.min(5, pg.page_count || 1));
        }
      })
      .catch((e) => {
        if (!cancelled) setSetupError(e instanceof Error ? e.message : "Could not load source");
      });
    return () => {
      cancelled = true;
    };
  }, [artifactId]);

  useEffect(() => {
    if (!selectedRange || artifact?.status === "indexing") return;
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
  }, [artifactId, selectedRange, artifact?.status]);

  useEffect(() => {
    if (!queue?.current_assertion_id) return;
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

  if (setupError && !artifact) {
    return (
      <Center mih="50vh">
        <Text c="red">{setupError}</Text>
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
      <Center mih="70vh">
        <Paper p="xl" maw={480} w="100%" withBorder>
          <Stack gap="md">
            <Title order={3}>Choose pages to study</Title>
            <Text c="dimmed">Questions and chat stay scoped to your selection.</Text>
            <Text size="sm" c="dimmed">
              Select pages ({pageCount} pages).
            </Text>
            <SimpleGrid cols={{ base: 6, sm: 8, md: 10 }} spacing="xs">
              {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
                <Button
                  key={p}
                  variant={p >= safeFrom && p <= safeTo ? "filled" : "light"}
                  size="compact-sm"
                  fullWidth
                  onClick={() => {
                    if (p < safeFrom) setPageFrom(p);
                    else setPageTo(p);
                  }}
                >
                  {p}
                </Button>
              ))}
            </SimpleGrid>
            <Group grow>
              <NumberInput label="From" min={1} max={pageCount} value={safeFrom} onChange={(v) => setPageFrom(typeof v === "number" ? v : 1)} />
              <NumberInput label="To" min={safeFrom} max={pageCount} value={safeTo} onChange={(v) => setPageTo(typeof v === "number" ? v : safeFrom)} />
            </Group>
            <Group justify="flex-end">
              <Button onClick={() => void confirmRange()}>Confirm range</Button>
            </Group>
            {confirming && <Text size="sm" c="dimmed">Starting indexing…</Text>}
            {setupError && <Text size="sm" c="red">{setupError}</Text>}
          </Stack>
        </Paper>
      </Center>
    );
  }

  if (artifact.status === "indexing") {
    return (
      <Center mih="70vh">
        <Stack align="center" gap="sm">
          <Loader />
          <Title order={3}>Indexing your pages…</Title>
          <Text c="dimmed">
            Pages {selectedRange.from}–{selectedRange.to} · This usually takes a minute.
          </Text>
        </Stack>
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
              <Alert variant="light" color={feedback.includes("Correct") ? "green" : "blue"}>
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
                  <Alert variant="light" color={feedback.includes("Correct") ? "green" : "blue"}>
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
                        <Paper key={i} p="sm" radius="md" bg={m.role === "user" ? "blue.9" : "dark.6"}>
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
                  <Alert variant="light" color={feedback.includes("Correct") ? "green" : "blue"}>
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
                      <Paper key={i} p="sm" radius="md" bg={m.role === "user" ? "blue.9" : "dark.6"}>
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
