"use client";

/**
 * Practice run page — answer MCQs for a concept, anonymously.
 *
 * Loads a batch of questions for the concept, renders them one at a time via the
 * shared McqCard, grades against /api/mcq/grade (guest cookie works as-is), and
 * tracks a running session score. When the batch is exhausted, shows a summary.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Anchor,
  Box,
  Button,
  Center,
  Container,
  Group,
  Paper,
  Progress,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { IconArrowLeft } from "@tabler/icons-react";
import { McqCard, type GradeState } from "@/app/_components/mcq/McqCard";
import { apiGet, apiPost, ensureGuestSession } from "@/lib/api/client";
import type { McqGradeResponse } from "@/lib/types";

type QuestionItem = {
  id: string;
  question: string;
  options: string[];
  correct_index: number;
  /** Present only for multi-select ("select all that apply") items. */
  correct_indices?: number[] | null;
  explanation: string;
};
type QuestionsResponse = { qid: string; question_count: number; items: QuestionItem[] };

export default function PracticeRunPage({ params }: { params: Promise<{ qid: string }> }) {
  const router = useRouter();
  const [qid, setQid] = useState<string>("");
  const [items, setItems] = useState<QuestionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [multiSelected, setMultiSelected] = useState<number[]>([]);
  const [gradeState, setGradeState] = useState<GradeState>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [score, setScore] = useState({ correct: 0, answered: 0 });
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    void (async () => {
      const { qid: q } = await params;
      setQid(q);
    })();
  }, [params]);

  const loadQuestions = useCallback(async () => {
    if (!qid) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<QuestionsResponse>(`/api/practice/concepts/${qid}/questions?limit=20`);
      if (data.items.length === 0) {
        // Concept is empty — bounce back to the concept page which triggers generation.
        router.replace(`/practice/c/${qid}`);
        return;
      }
      setItems(data.items);
      setTotal(data.question_count);
    } catch {
      setError("Could not load questions. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [qid, router]);

  useEffect(() => {
    if (!qid) return;
    void ensureGuestSession().then(() => {
      void loadQuestions();
    });
  }, [qid, loadQuestions]);

  const current = items[index];
  const isMulti = Array.isArray(current?.correct_indices) && (current?.correct_indices?.length ?? 0) >= 2;

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
      const correctIndex = res.correct_index ?? (isMulti ? (multiSelected[0] ?? 0) : Number(selected));
      setFeedback(res.feedback ?? (correct ? "Correct!" : "Not quite — see the explanation."));
      setGradeState({ correct, correctIndex, correctIndices: res.correct_indices });
      setScore((s) => ({ correct: s.correct + (correct ? 1 : 0), answered: s.answered + 1 }));
    } catch {
      setFeedback("Could not grade your answer — please try again.");
    } finally {
      setSubmitting(false);
    }
  }, [current, isMulti, multiSelected, selected, submitting]);

  const handleNext = useCallback(() => {
    setFeedback(null);
    setGradeState(null);
    setSelected(null);
    setMultiSelected([]);
    if (index + 1 >= items.length) {
      setFinished(true);
    } else {
      setIndex((i) => i + 1);
    }
  }, [index, items.length]);

  const restart = useCallback(() => {
    setIndex(0);
    setSelected(null);
    setMultiSelected([]);
    setGradeState(null);
    setFeedback(null);
    setScore({ correct: 0, answered: 0 });
    setFinished(false);
  }, []);

  const pct = useMemo(
    () => (items.length > 0 ? Math.round(((index + (gradeState ? 1 : 0)) / items.length) * 100) : 0),
    [index, items.length, gradeState],
  );

  if (loading) {
    return (
      <Shell>
        <Center style={{ height: "70vh" }}>
          <Stack align="center" gap="sm">
            <Progress value={100} size="sm" radius="xl" w={160} animated color="lavender" />
            <Text size="sm" c="dimmed">
              Loading your questions…
            </Text>
          </Stack>
        </Center>
      </Shell>
    );
  }

  if (error) {
    return (
      <Shell>
        <Center style={{ height: "60vh" }}>
          <Stack align="center" gap="md">
            <Text c="terracotta.7">{error}</Text>
            <Button variant="light" onClick={() => router.push(`/practice/c/${qid}`)}>
              Back to concept
            </Button>
          </Stack>
        </Center>
      </Shell>
    );
  }

  if (finished) {
    const pctScore = score.answered > 0 ? Math.round((score.correct / score.answered) * 100) : 0;
    return (
      <Shell>
        <Container size="sm" py={{ base: 48, md: 72 }}>
          <Stack align="center" gap="lg" ta="center">
            <Text size="xs" fw={600} tt="uppercase" lts={1.5} c="lavender.8">
              Session complete
            </Text>
            <Title
              order={1}
              fw={500}
              style={{ fontFamily: "var(--font-serif), Georgia, serif", letterSpacing: "-0.02em" }}
            >
              You scored{" "}
              <Box component="span" c="lavender.7">
                {score.correct}
              </Box>{" "}
              / {score.answered}
            </Title>
            <Text size="lg" c="gray.6" lh={1.6}>
              That&apos;s {pctScore}% correct on this concept.
            </Text>
            <Group gap="sm" mt="md">
              <Button radius="xl" onClick={restart}>
                Practice again
              </Button>
              <Button radius="xl" variant="light" onClick={() => router.push(`/practice/c/${qid}`)}>
                Back to concept
              </Button>
            </Group>
          </Stack>
        </Container>
      </Shell>
    );
  }

  return (
    <Shell>
      <Container size="md" py={{ base: 24, md: 36 }}>
        <Stack gap="lg">
          {/* Top bar */}
          <Group justify="space-between" align="center">
            <Anchor href={`/practice/c/${qid}`} size="sm" c="gray.6" underline="hover">
              <Group gap={6}>
                <IconArrowLeft size={14} />
                Concept
              </Group>
            </Anchor>
            <Group gap="xs">
              <Text size="sm" c="dimmed">
                Question {index + 1} of {items.length}
              </Text>
              {score.answered > 0 && (
                <Text size="sm" c="lavender.7" fw={500}>
                  {score.correct}/{score.answered} correct
                </Text>
              )}
            </Group>
          </Group>

          <Progress value={pct} size="xs" radius="xl" color="lavender" />

          {/* Question card */}
          <Paper shadow="paper-lg" radius="xl" p={{ base: "lg", md: "xl" }} withBorder>
            <McqCard
              stem={current?.question ?? ""}
              options={current?.options ?? []}
              selected={selected}
              onSelect={setSelected}
              multiSelect={isMulti}
              selectedIndices={multiSelected}
              onToggle={toggleMulti}
              feedback={feedback}
              mode="test"
              gradeState={gradeState}
              submitting={submitting}
              loading={!current}
              loadingLabel="Preparing your questions…"
              onSubmit={handleSubmit}
              onNext={handleNext}
            />
          </Paper>
        </Stack>
      </Container>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <Box
      bg="var(--mantine-color-body)"
      style={{ height: "100dvh", overflowY: "auto", overflowX: "hidden" }}
    >
      {children}
    </Box>
  );
}
