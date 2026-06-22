/** Learn-mode wait copy — describe real background work, not vague placeholders. */

import { indexingStage } from "@/lib/constants";

export type LearnWaitContext = {
  artifactStatus?: string;
  indexProgress?: number;
  mcqLoading?: boolean;
  generationPending?: boolean;
  pageTriageComplete?: boolean;
  ragWindowReady?: boolean;
  questionsGenerated?: number;
  questionBudget?: number;
  poolAvailable?: number;
};

export type LearnWaitStatus = {
  title: string;
  detail: string;
  /** Stable key — reset rotation when this changes */
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
    const budget = ctx.questionBudget ?? 0;
    const line =
      GENERATING_LINES[((tick % GENERATING_LINES.length) + GENERATING_LINES.length) % GENERATING_LINES.length];
    if (generated === 0) {
      return { title: line.title, detail: line.detail, rotateKey: "gen-first" };
    }
    const progress =
      budget > 0 ? `${generated} ready · working toward ${budget} on this page` : `${generated} ready · writing more`;
    return { title: line.title, detail: progress, rotateKey: "gen-more" };
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
