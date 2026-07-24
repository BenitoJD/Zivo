"use client";

/**
 * Embedded MCQs on /learn/[slug] — guest grade via existing /api/mcq/grade.
 */

import { useCallback, useEffect, useState } from "react";
import { Stack, Text, Title } from "@mantine/core";
import { McqCard, type GradeState } from "@/app/_components/mcq/McqCard";
import { apiGet, apiPost, ensureGuestSession } from "@/lib/api/client";
import type { McqGradeResponse } from "@/lib/types";

type QItem = {
  id: string;
  question: string;
  options: string[];
  is_multi?: boolean;
};

export function LearnMcqSection({ slug }: { slug: string }) {
  const [items, setItems] = useState<QItem[]>([]);
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [multiSelected, setMultiSelected] = useState<number[]>([]);
  const [gradeState, setGradeState] = useState<GradeState>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    void ensureGuestSession();
    void (async () => {
      try {
        const res = await apiGet<{ items: QItem[] }>(
          `/api/learn/posts/${encodeURIComponent(slug)}/questions`,
        );
        setItems(res.items ?? []);
      } catch {
        setItems([]);
      } finally {
        setLoaded(true);
      }
    })();
  }, [slug]);

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
    } catch {
      setFeedback("Could not grade. Try again.");
    } finally {
      setSubmitting(false);
    }
  }, [current, isMulti, multiSelected, selected, submitting]);

  const handleNext = useCallback(() => {
    setFeedback(null);
    setGradeState(null);
    setSelected(null);
    setMultiSelected([]);
    if (index + 1 < items.length) setIndex(index + 1);
  }, [index, items.length]);

  if (!loaded || items.length === 0) return null;

  return (
    <Stack gap="md" mt="xl">
      <Title order={2} ff="var(--font-serif)" fw={500} size="h3">
        Try a question
      </Title>
      <Text size="sm" c="dimmed">
        Question {index + 1} of {items.length}
      </Text>
      {current ? (
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
      ) : null}
    </Stack>
  );
}
