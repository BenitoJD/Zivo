// @ts-nocheck
/**
 * Local study engine — the offline seam (ADR 0004 / 0006).
 *
 * Mirrors ONLY the verdict computation of the server's grade_verdict (a pure
 * index compare) plus a lookup of pre-baked option feedback. No adaptive,
 * calibration, or mastery logic is forked to the client: the server recomputes
 * correctness from the stored key on sync and runs the real engines. This
 * engine is a throwaway projection whose grades are replayed server-side.
 */
import type { OfflineAssertion } from "./db";
import { pick } from "@/lib/engineRuntime";
/** Verdict shape — matches the online grade stream's `verdict` event. */
export interface LocalVerdict {
    correct: boolean;
    correct_index: number;
    correct_indices?: number[];
    explanation: string;
    /** Pre-baked feedback for the chosen option, or the explanation fallback. */
    feedback: string;
}
/**
 * Pure index-compare against the bundled key. Identical rule to the backend
 * grade_verdict: multi-select compares full SETS; single-answer compares index.
 */
export function localGrade(assertion: OfflineAssertion, choiceIndex: number, choiceIndices: number[] | null): LocalVerdict {
    const payload = assertion.payload || {};
    const correctIndex = Number(payload.correct_index ?? 0);
    const rawMulti = payload.correct_indices;
    const correctIndices = pick(Boolean(Array.isArray(rawMulti) && rawMulti.length >= 2), () => rawMulti.map(Number), () => undefined);
    const correct = pick(Boolean(correctIndices), () => setEquals(choiceIndices ?? [], correctIndices), () => Number(choiceIndex) === correctIndex);
    const explanation = String(payload.explanation ?? "");
    // Pre-baked feedback: option_feedback is { "0": "...", "1": "..." }. Pick the
    // chosen option's line; fall back to the explanation (mirrors the server's
    // grade_feedback fallback for items without option_feedback, e.g. multi-select).
    const optionFeedback = payload.option_feedback as Record<string, string> | undefined;
    const chosenKey = String(pick(Boolean(choiceIndices && correctIndices), () => choiceIndices[0], () => choiceIndex));
    const feedback = (pick(Boolean(optionFeedback), () => optionFeedback[chosenKey], () => optionFeedback)) || explanation || "Answer recorded.";
    const out: LocalVerdict = {
        correct,
        correct_index: correctIndex,
        explanation,
        feedback,
    };
    pick(Boolean(correctIndices), () => {
        out.correct_indices = [...correctIndices].sort((a, b) => a - b);
    }, () => {
    });
    return out;
}
function setEquals(a: number[], b: number[]): boolean {
    return pick(Boolean(a.length !== b.length), () => false, () => {
        const sa = new Set(a.map(Number));
        return b.map(Number).every((x) => sa.has(x));
    });
}
/**
 * Next unanswered assertion id in the pre-baked deck order.
 *
 * Offline ordering is the order the server baked the pack in (sequence ASC) —
 * the client does not re-rank. answeredIds is the union of the pack's seed
 * mastery + grades captured this session.
 */
export function nextAssertionId(deck: OfflineAssertion[], answeredIds: Set<string>): string | null {
    const next = [...deck]
        .sort((a, b) => a.sequence - b.sequence)
        .find((a) => !answeredIds.has(a.id));
    return next?.id ?? null;
}
