"use client";

/**
 * Newspaper edition run — MCQs + tutor chat. No PDF.
 */

import { useCallback, useEffect, useMemo, useState, use } from "react";
import {
  Anchor,
  Box,
  Button,
  Container,
  Group,
  Paper,
  Progress,
  ScrollArea,
  Stack,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { IconArrowLeft } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { McqCard, type GradeState } from "@/app/_components/mcq/McqCard";
import { apiGet, apiPost, apiPostSSE, ensureGuestSession } from "@/lib/api/client";
import { useNewspaperQuestionsQuery } from "@/lib/api/queries";
import type { McqGradeResponse } from "@/lib/types";
import { Shell } from "@/app/practice/_components/Shell";

type ChatMsg = { role: string; content: string };

export default function NewspaperEditionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const q = useNewspaperQuestionsQuery(id);

  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [multiSelected, setMultiSelected] = useState<number[]>([]);
  const [gradeState, setGradeState] = useState<GradeState>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [score, setScore] = useState({ correct: 0, answered: 0 });
  const [finished, setFinished] = useState(false);

  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
  const [chatBusy, setChatBusy] = useState(false);

  const edition = q.data?.edition;
  const items = q.data?.items ?? [];
  const docId = edition?.document_id;

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  useEffect(() => {
    if (!docId) return;
    void (async () => {
      try {
        const msgs = await apiGet<{ role: string; content: string }[]>(
          `/api/chat/threads/${docId}/messages?surface=learn`,
        );
        setChatMessages(
          (msgs || [])
            .filter((m) => (m.content || "").trim())
            .map((m) => ({ role: m.role, content: m.content })),
        );
      } catch {
        /* empty thread ok */
      }
    })();
  }, [docId]);

  const current = items[index];
  const isMulti = Boolean(current?.is_multi);

  const toggleMulti = useCallback((i: number) => {
    setMultiSelected((prev) =>
      prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i].sort((a, b) => a - b),
    );
  }, []);

  const handleSubmit = useCallback(async () => {
    const hasSelection = isMulti ? multiSelected.length > 0 : selected !== null;
    if (!current || !hasSelection || submitting) return;
    setSubmitting(true);
    try {
      const res = await apiPost<McqGradeResponse>("/api/mcq/grade", {
        assertion_id: current.id,
        choice_index: isMulti ? (multiSelected[0] ?? -1) : Number(selected),
        ...(isMulti ? { choice_indices: multiSelected } : {}),
        mode: "test",
      });
      const correct = Boolean(res.correct);
      setFeedback(res.feedback ?? (correct ? "Correct!" : "Not quite."));
      setGradeState({
        correct,
        correctIndex: res.correct_index ?? 0,
        correctIndices: res.correct_indices,
      });
      setScore((s) => ({ correct: s.correct + (correct ? 1 : 0), answered: s.answered + 1 }));
    } catch {
      setFeedback("Could not grade — try again.");
    } finally {
      setSubmitting(false);
    }
  }, [current, isMulti, multiSelected, selected, submitting]);

  const handleNext = useCallback(() => {
    setFeedback(null);
    setGradeState(null);
    setSelected(null);
    setMultiSelected([]);
    if (index + 1 >= items.length) setFinished(true);
    else setIndex((i) => i + 1);
  }, [index, items.length]);

  const pct = useMemo(
    () => (items.length > 0 ? Math.round(((index + (gradeState ? 1 : 0)) / items.length) * 100) : 0),
    [index, items.length, gradeState],
  );

  async function sendChat() {
    const msg = chatInput.trim();
    if (!msg || !docId || chatBusy) return;
    setChatInput("");
    setChatMessages((m) => [...m, { role: "user", content: msg }, { role: "assistant", content: "" }]);
    setChatBusy(true);
    try {
      let acc = "";
      await apiPostSSE(
        "/api/chat",
        { document_id: docId, message: msg, scope: { mode: "learn" } },
        {
          onChunk: (chunk) => {
            acc += chunk;
            setChatMessages((m) => {
              const copy = [...m];
              copy[copy.length - 1] = { role: "assistant", content: acc };
              return copy;
            });
          },
        },
      );
    } catch {
      setChatMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = {
          role: "assistant",
          content: "Tutor is unavailable right now.",
        };
        return copy;
      });
    } finally {
      setChatBusy(false);
    }
  }

  const backHref = edition?.paper_slug
    ? `/practice/newspaper/${edition.paper_slug}`
    : "/practice/newspaper";

  return (
    <Shell>
      <Container size="md" py={{ base: 28, md: 48 }}>
        <Stack gap="lg">
          <Anchor component="button" c="dimmed" fz="sm" onClick={() => router.push(backHref)}>
            <Group gap={6}>
              <IconArrowLeft size={14} />
              {edition?.paper_title || "Back"}
            </Group>
          </Anchor>

          <Box>
            <Title order={2} ff="var(--font-serif)" fw={500}>
              {edition?.paper_title || "Newspaper"}
            </Title>
            <Text c="dimmed" fz="sm">
              {edition?.edition_date || "…"} · questions only
            </Text>
          </Box>

          {q.isLoading ? (
            <Text c="dimmed">Loading questions…</Text>
          ) : q.isError ? (
            <Text c="terracotta">Couldn&rsquo;t load this edition.</Text>
          ) : edition?.status === "indexing" || edition?.status === "pending" ? (
            <Text c="dimmed">Still preparing questions for this day…</Text>
          ) : items.length === 0 ? (
            <Text c="dimmed">No questions ready yet. Check back shortly.</Text>
          ) : finished ? (
            <Paper radius="xl" p="xl" withBorder bg="gray.0" shadow="paper">
              <Stack gap="md">
                <Title order={3} ff="var(--font-serif)" fw={500}>
                  Done for now
                </Title>
                <Text>
                  {score.correct} / {score.answered} correct
                </Text>
                <Group>
                  <Button radius="xl" onClick={() => router.push(backHref)}>
                    Pick another day
                  </Button>
                  <Button
                    radius="xl"
                    variant="light"
                    onClick={() => {
                      setFinished(false);
                      setIndex(0);
                      setScore({ correct: 0, answered: 0 });
                      setGradeState(null);
                      setFeedback(null);
                    }}
                  >
                    Practice again
                  </Button>
                </Group>
              </Stack>
            </Paper>
          ) : (
            <Stack gap="md">
              <Progress value={pct} radius="xl" color="lavender" />
              <McqCard
                stem={current.question}
                options={current.options}
                selected={selected}
                onSelect={setSelected}
                multiSelect={isMulti}
                selectedIndices={multiSelected}
                onToggle={toggleMulti}
                feedback={feedback}
                mode="test"
                gradeState={gradeState}
                submitting={submitting}
                onSubmit={() => void handleSubmit()}
                onNext={handleNext}
              />
            </Stack>
          )}

          {docId ? (
            <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
              <Stack gap="sm">
                <Text fw={600} ff="var(--font-serif)">
                  Ask the tutor
                </Text>
                <ScrollArea h={180} offsetScrollbars>
                  <Stack gap="xs">
                    {chatMessages.length === 0 ? (
                      <Text size="sm" c="dimmed">
                        Stuck on a story? Ask here — answers stay grounded in today&rsquo;s paper.
                      </Text>
                    ) : (
                      chatMessages.map((m, i) => (
                        <Box key={`${i}-${m.role}`}>
                          <Text size="xs" c="dimmed" tt="uppercase" lts={0.5}>
                            {m.role === "user" ? "You" : "Tutor"}
                          </Text>
                          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                            {m.content || (chatBusy && i === chatMessages.length - 1 ? "…" : "")}
                          </Text>
                        </Box>
                      ))
                    )}
                  </Stack>
                </ScrollArea>
                <Textarea
                  placeholder="Ask about today’s paper…"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.currentTarget.value)}
                  minRows={2}
                  radius="md"
                  disabled={chatBusy}
                />
                <Button
                  radius="xl"
                  loading={chatBusy}
                  disabled={!chatInput.trim()}
                  onClick={() => void sendChat()}
                >
                  Send
                </Button>
              </Stack>
            </Paper>
          ) : null}
        </Stack>
      </Container>
    </Shell>
  );
}
