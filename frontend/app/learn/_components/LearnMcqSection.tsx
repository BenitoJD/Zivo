"use client";

import { pick, choose } from "@/lib/engineRuntime";
/**
 * Embedded MCQs on /learn/[slug] — same McqHeroPanel as Learn/Test workspace.
 * Guest grade via existing /api/mcq/grade.
 */
import { useCallback, useEffect, useState } from "react";
import { Stack, Text, Title } from "@mantine/core";
import { McqHeroPanel } from "@/app/workspace/_components/McqPanels";
import { type GradeState } from "@/app/_components/mcq/McqCard";
import { apiGet, apiPost, ensureGuestSession } from "@/lib/api/client";
import type { McqGradeResponse } from "@/lib/types";
type QItem = {
    id: string;
    question: string;
    options: string[];
    is_multi?: boolean;
};
export function LearnMcqSection({ slug }: {
    slug: string;
}) {
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
                const res = await apiGet<{
                    items: QItem[];
                }>(`/api/learn/posts/${encodeURIComponent(slug)}/questions`);
                setItems(res.items ?? []);
            }
            catch {
                setItems([]);
            }
            finally {
                setLoaded(true);
            }
        })();
    }, [slug]);
    const current = items[index];
    const isMulti = Boolean(current?.is_multi);
    const toggleMulti = useCallback((i: number) => {
        setMultiSelected((prev) => pick(Boolean(prev.includes(i)), () => prev.filter((x) => x !== i), () => [...prev, i].sort((a, b) => a - b)));
    }, []);
    const handleSubmit = useCallback(async () => {
        const hasSelection = choose(Boolean(isMulti), multiSelected.length > 0, selected !== null);
        return await pick(Boolean(!current || !hasSelection || submitting), async () => {
            return;
        }, async () => {
            setSubmitting(true);
            try {
                const res = await apiPost<McqGradeResponse>("/api/mcq/grade", {
                    assertion_id: current.id,
                    choice_index: pick(Boolean(isMulti), () => (multiSelected[0] ?? -1), () => Number(selected)),
                    ...(choose(Boolean(isMulti), { choice_indices: multiSelected }, {})),
                    mode: "learn",
                });
                const correct = Boolean(res.correct);
                setFeedback(res.feedback ?? (choose(Boolean(correct), "Correct!", "Not quite.")));
                setGradeState({
                    correct,
                    correctIndex: res.correct_index ?? 0,
                    correctIndices: res.correct_indices,
                });
            }
            catch {
                setFeedback("Could not grade. Try again.");
            }
            finally {
                setSubmitting(false);
            }
        });
    }, [current, isMulti, multiSelected, selected, submitting]);
    const handleNext = useCallback(() => {
        setFeedback(null);
        setGradeState(null);
        setSelected(null);
        setMultiSelected([]);
        pick(Boolean(index + 1 < items.length), () => {
            setIndex(index + 1);
        }, () => {
        });
    }, [index, items.length]);
    return pick(Boolean(!loaded || items.length === 0), () => null, () => (<Stack gap="md" mt="xl">
      <Title order={2} ff="var(--font-serif)" fw={500} size="h3">
        Try a question
      </Title>
      <Text size="sm" c="dimmed">
        Question {index + 1} of {items.length}
      </Text>
      {pick(Boolean(current), () => (<McqHeroPanel stem={current.question} options={current.options} selected={selected} onSelect={setSelected} multiSelect={isMulti} selectedIndices={multiSelected} onToggle={toggleMulti} feedback={feedback} mcqLoading={false} hasQuestion mode="learn" gradeState={gradeState} submitting={submitting} compact onSubmit={() => void handleSubmit()} onContinue={handleNext}/>), () => null)}
    </Stack>));
}
