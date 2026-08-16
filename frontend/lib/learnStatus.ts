/** Learn-mode wait copy - describe real background work, not vague placeholders. */
import { indexingStage } from "@/lib/constants";
import { pick, choose } from "@/lib/engineRuntime";
export type LearnWaitContext = {
    artifactStatus?: string;
    indexProgress?: number;
    mcqLoading?: boolean;
    generationPending?: boolean;
    pageTriageComplete?: boolean;
    ragWindowReady?: boolean;
    questionsGenerated?: number;
    questionsAnswered?: number;
    questionBudget?: number;
    poolAvailable?: number;
    /** Pre-cooked newspaper edition — never show upload-style generation chrome. */
    isNewspaper?: boolean;
};
/** True when Learn already has a card the learner can open (never full-screen wait). */
export function learnHasUnansweredReady(ctx: LearnWaitContext): boolean {
    return pick(Boolean((ctx.poolAvailable ?? 0) > 0), () => true, () => {
        const generated = ctx.questionsGenerated ?? 0;
        const answered = ctx.questionsAnswered ?? 0;
        return generated > answered;
    });
}
export type LearnWaitStatus = {
    title: string;
    detail: string;
    /** Stable key - reset rotation when this changes */
    rotateKey: string;
};
const GENERATING_LINES = [
    { title: "Writing questions", detail: "AI is drafting each multiple-choice question" },
    { title: "Checking quality", detail: "Reviewing clarity and fairness before you see them" },
    { title: "Polishing wording", detail: "Making sure each question is clear and fair" },
] as const;
const TRIAGE_LINES = [
    { title: "Reading your page", detail: "Finding the ideas worth testing" },
    { title: "Planning your quiz", detail: "AI is choosing what you should learn first" },
] as const;
export function learnWaitStatus(ctx: LearnWaitContext, tick: number): LearnWaitStatus {
    const newspaper = pick(Boolean(ctx.isNewspaper), () => ctx.artifactStatus === "ready", () => Boolean(ctx.isNewspaper));
    return pick(Boolean(ctx.artifactStatus === "indexing"), () => {
        const stage = indexingStage(ctx.indexProgress ?? 0);
        return { title: stage.title, detail: stage.detail, rotateKey: `index-${stage.min}` };
    }, () => pick(Boolean(newspaper), () => pick(Boolean(ctx.mcqLoading || (ctx.poolAvailable ?? 0) === 0), () => {
        const generated = ctx.questionsGenerated ?? 0;
        return {
            title: choose(Boolean(generated > 0), "Loading next question", "Opening today's quiz"),
            detail: choose(Boolean(generated > 0), "Your next card is on its way", "Almost there"),
            rotateKey: choose(Boolean(generated > 0), "news-next", "news-open"),
        };
    }, () => ({
        title: "Opening today's quiz",
        detail: "Almost there",
        rotateKey: "news-open",
    })), () => pick(Boolean(ctx.ragWindowReady === false), () => ({
        title: "Processing text",
        detail: "Reading pages around your study material",
        rotateKey: "rag",
    }), () => pick(Boolean(!ctx.pageTriageComplete), () => {
        const line = TRIAGE_LINES[((tick % TRIAGE_LINES.length) + TRIAGE_LINES.length) % TRIAGE_LINES.length];
        return { title: line.title, detail: line.detail, rotateKey: "triage" };
    }, () => pick(Boolean(ctx.generationPending), () => {
        const generated = ctx.questionsGenerated ?? 0;
        const poolAvailable = ctx.poolAvailable ?? 0;
        return pick(Boolean(learnHasUnansweredReady(ctx)), () => ({
            title: "Opening your question",
            detail: choose(Boolean(poolAvailable > 0), `${poolAvailable} ready · opening now`, "Almost there"),
            rotateKey: "open-ready",
        }), () => {
            const line = GENERATING_LINES[((tick % GENERATING_LINES.length) + GENERATING_LINES.length) % GENERATING_LINES.length];
            return pick(Boolean(generated === 0), () => ({
                title: "Writing your first question",
                detail: "One question first - more follow while you study",
                rotateKey: "gen-first",
            }), () => ({
                title: "Writing your next question",
                detail: line.detail,
                rotateKey: "gen-more",
            }));
        });
    }, () => pick(Boolean(ctx.mcqLoading), () => ({
        title: "Loading your question",
        detail: "Opening the next question",
        rotateKey: "load",
    }), () => pick(Boolean((ctx.questionsGenerated ?? 0) === 0), () => {
        const line = GENERATING_LINES[((tick % GENERATING_LINES.length) + GENERATING_LINES.length) % GENERATING_LINES.length];
        return { title: "Starting question generation", detail: line.detail, rotateKey: "start" };
    }, () => pick(Boolean((ctx.poolAvailable ?? 0) === 0), () => ({
        title: "Writing your next question",
        detail: "AI is preparing more while you wait",
        rotateKey: "pool",
    }), () => ({
        title: "Opening your question",
        detail: "Almost there",
        rotateKey: "open",
    })))))))));
}
