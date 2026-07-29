"use client";

/**
 * Practice run page - answer MCQs for a concept, anonymously.
 *
 * Same McqHeroPanel as Learn/Test workspace. Grades via /api/mcq/grade
 * (guest cookie works as-is). Session score until batch exhausted.
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
  Progress,
  Stack,
  Text,
} from "@mantine/core";
import { IconArrowLeft } from "@tabler/icons-react";
import { McqHeroPanel } from "@/app/workspace/_components/McqPanels";
import { type GradeState } from "@/app/_components/mcq/McqCard";
import { LearnerPageHeader } from "@/app/_components/study/LearnerPageHeader";
import { apiGet, apiPost, ensureGuestSession } from "@/lib/api/client";
import type { McqGradeResponse } from "@/lib/types";
import { Shell } from "@/app/practice/_components/Shell";

type QuestionItem = {
  id: string;
  question: string;
  options: string[];
  /** True for select-all-that-apply; answers come from the grade endpoint. */
  is_multi?: boolean;
};
type QuestionsResponse = {
  qid: string;
  question_count: number;
  items: QuestionItem[];
  answered_ids?: string[];
  resume_index?: number;
};

export default function PracticeRunPage({ params }: { params: Promise<{ qid: string }> }) {
  const router = useRouter();
  const [qid, setQid] = useState<string>("");
  const [items, setItems] = useState<QuestionItem[]>([]);
  const [, setTotal] = useState(0);
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
  const [alreadyComplete, setAlreadyComplete] = useState(false);

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
        // Concept is empty - bounce back to the concept page which triggers generation.
        router.replace(`/practice/c/${qid}`);
        return;
      }
      setItems(data.items);
      setTotal(data.question_count);
      const resumeAt = data.resume_index ?? 0;
      if (resumeAt > 0 && resumeAt < data.items.length) {
        setIndex(resumeAt);
      } else if (resumeAt >= data.items.length && data.items.length > 0) {
        setAlreadyComplete(true);
        setFinished(true);
        setScore({ correct: 0, answered: data.answered_ids?.length ?? data.items.length });
      }
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
        mode: "learn",
      });
      const correct = Boolean(res.correct);
      const correctIndex = res.correct_index ?? (isMulti ? (multiSelected[0] ?? 0) : Number(selected));
      setFeedback(res.feedback ?? (correct ? "Correct!" : "Not quite - see the explanation."));
      setGradeState({ correct, correctIndex, correctIndices: res.correct_indices });
      setScore((s) => ({ correct: s.correct + (correct ? 1 : 0), answered: s.answered + 1 }));
    } catch {
      setFeedback("Could not grade your answer - please try again.");
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
    setAlreadyComplete(false);
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
            <Button variant="light" radius="xl" color="lavender" onClick={() => router.push(`/practice/c/${qid}`)}>
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
            <LearnerPageHeader
              eyebrow="Session complete"
              title={alreadyComplete ? "You're caught up" : "You scored"}
              titleAccent={alreadyComplete ? undefined : `${score.correct} / ${score.answered}`}
              subtitle={
                alreadyComplete
                  ? `You already answered ${score.answered} question${score.answered === 1 ? "" : "s"} on this concept.`
                  : `That's ${pctScore}% correct on this concept.`
              }
            />
            <Group gap="sm" mt="md">
              <Button radius="xl" color="sage" onClick={restart}>
                Practice again
              </Button>
              <Button radius="xl" variant="light" color="lavender" onClick={() => router.push(`/practice/c/${qid}`)}>
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
          <Group justify="space-between" align="center">
            <Anchor href={`/practice/c/${qid}`} size="sm" c="dimmed" underline="hover">
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

          <Box mih={320}>
            <McqHeroPanel
              stem={current?.question ?? ""}
              options={current?.options ?? []}
              selected={selected}
              onSelect={setSelected}
              multiSelect={isMulti}
              selectedIndices={multiSelected}
              onToggle={toggleMulti}
              feedback={feedback}
              mcqLoading={!current}
              hasQuestion={Boolean(current)}
              mode="learn"
              gradeState={gradeState}
              submitting={submitting}
              onSubmit={() => void handleSubmit()}
              onContinue={handleNext}
            />
          </Box>
        </Stack>
      </Container>
    </Shell>
  );
}
