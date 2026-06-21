"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api/client";

type McqState = {
  current_assertion_id: string | null;
  concepts: { concept_key: string; label: string }[];
  page_mastered: boolean;
  page_ready: boolean;
};

type AssertionPayload = {
  question?: string;
  stem?: string;
  options?: string[];
  choices?: string[];
};

export function McqPanel({
  artifactId,
  mode,
}: {
  artifactId?: string;
  mode: "learn" | "test";
}) {
  const [queue, setQueue] = useState<McqState | null>(null);
  const [question, setQuestion] = useState<string>("Upload a source to begin.");
  const [options, setOptions] = useState<string[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!artifactId) return;
    setLoading(true);
    apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`)
      .then(setQueue)
      .catch(() => setQuestion("Sign in or reload to load questions."))
      .finally(() => setLoading(false));
  }, [artifactId]);

  useEffect(() => {
    if (!queue?.current_assertion_id) return;
    apiGet<{ payload: AssertionPayload; title?: string }>(
      `/api/assertions/${queue.current_assertion_id}`,
    )
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

  async function submit() {
    if (!queue?.current_assertion_id || selected === null) return;
    const res = await apiPost<{ correct?: boolean; feedback?: string }>("/api/mcq/grade", {
      assertion_id: queue.current_assertion_id,
      choice_index: selected,
    });
    setFeedback(res.feedback ?? (res.correct ? "Correct!" : "Try again."));
    if (artifactId) {
      apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`).then(setQueue).catch(() => {});
    }
  }

  return (
    <section className="mcq-panel">
      <div className="mcq-panel__progress">
        {mode === "learn" && queue && (
          <span>
            Concepts: {queue.concepts.length} · Page mastered: {queue.page_mastered ? "yes" : "no"}
          </span>
        )}
      </div>
      <h2 className="mcq-panel__stem">
        {loading
          ? "Loading questions…"
          : !artifactId
            ? "Upload a source to begin."
            : queue && !queue.current_assertion_id
              ? "Questions will appear once indexing finishes."
              : question}
      </h2>
      <div className="mcq-panel__choices">
        {options.map((opt, i) => (
          <button
            key={i}
            type="button"
            className={`mcq-choice${selected === i ? " is-selected" : ""}`}
            onClick={() => setSelected(i)}
          >
            <kbd>{i + 1}</kbd> {opt}
          </button>
        ))}
      </div>
      {feedback && <p className="mcq-panel__feedback">{feedback}</p>}
      <button type="button" className="mcq-panel__submit" onClick={submit} disabled={selected === null}>
        Submit
      </button>
      {queue?.page_mastered && !queue.page_ready && (
        <button
          type="button"
          className="mcq-panel__ready-cta"
          onClick={() =>
            artifactId &&
            apiPost(`/api/artifacts/${artifactId}/pages/1/advance`, {}).then(() =>
              apiGet<McqState>(`/api/artifacts/${artifactId}/learn-queue`).then(setQueue),
            )
          }
        >
          I&apos;m ready for the next page
        </button>
      )}
    </section>
  );
}
