/** Learn-mode wait copy - describe real background work, not vague placeholders. */

import { indexingStage } from "@/lib/constants";

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
};

/** True when Learn already has a card the learner can open (never full-screen wait). */
export function learnHasUnansweredReady(ctx: LearnWaitContext): boolean {
  if ((ctx.poolAvailable ?? 0) > 0) return true;
  const generated = ctx.questionsGenerated ?? 0;
  const answered = ctx.questionsAnswered ?? 0;
  return generated > answered;
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
  if (ctx.artifactStatus === "indexing") {
    const stage = indexingStage(ctx.indexProgress ?? 0);
    return { title: stage.title, detail: stage.detail, rotateKey: `index-${stage.min}` };
  }

  if (ctx.ragWindowReady === false) {
    return {
      title: "Processing text",
      detail: "Reading pages around your study material",
      rotateKey: "rag",
    };
  }

  if (!ctx.pageTriageComplete) {
    const line = TRIAGE_LINES[((tick % TRIAGE_LINES.length) + TRIAGE_LINES.length) % TRIAGE_LINES.length];
    return { title: line.title, detail: line.detail, rotateKey: "triage" };
  }

  if (ctx.generationPending) {
    const generated = ctx.questionsGenerated ?? 0;
    const poolAvailable = ctx.poolAvailable ?? 0;
    // Pool / unanswered already has a card — caller should not be in wait chrome.
    // Keep copy focused on first-question / truly-empty cases only.
    if (learnHasUnansweredReady(ctx)) {
      return {
        title: "Opening your question",
        detail:
          poolAvailable > 0
            ? `${poolAvailable} ready · opening now`
            : "Almost there",
        rotateKey: "open-ready",
      };
    }
    const line =
      GENERATING_LINES[((tick % GENERATING_LINES.length) + GENERATING_LINES.length) % GENERATING_LINES.length];
    if (generated === 0) {
      return {
        title: "Writing your first question",
        detail: "One question first - more follow while you study",
        rotateKey: "gen-first",
      };
    }
    // Generated but none unanswered (answered through the pool) while more cook.
    return {
      title: "Writing your next question",
      detail: line.detail,
      rotateKey: "gen-more",
    };
  }

  if (ctx.mcqLoading) {
    return {
      title: "Loading your question",
      detail: "Opening the next question",
      rotateKey: "load",
    };
  }

  if ((ctx.questionsGenerated ?? 0) === 0) {
    const line =
      GENERATING_LINES[((tick % GENERATING_LINES.length) + GENERATING_LINES.length) % GENERATING_LINES.length];
    return { title: "Starting question generation", detail: line.detail, rotateKey: "start" };
  }

  if ((ctx.poolAvailable ?? 0) === 0) {
    return {
      title: "Writing your next question",
      detail: "AI is preparing more while you wait",
      rotateKey: "pool",
    };
  }

  return {
    title: "Opening your question",
    detail: "Almost there",
    rotateKey: "open",
  };
}
