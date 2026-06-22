/** Learn-mode wait copy — one calm line at a time, no page metadata. */

export type LearnWaitPhase = "indexing" | "planning" | "generating" | "finishing";

const PHASE_LINES: Record<LearnWaitPhase, string[]> = {
  indexing: ["Opening your source", "Reading", "Just a moment"],
  planning: ["Finding what matters", "Understanding", "Getting oriented"],
  generating: ["Crafting your questions", "Almost there", "One more moment"],
  finishing: ["Ready", "Here we go", "Finishing up"],
};

export function learnWaitPhase(input: {
  indexing: boolean;
  planning: boolean;
  generating: boolean;
}): LearnWaitPhase {
  if (input.indexing) return "indexing";
  if (input.generating) return "generating";
  if (input.planning) return "planning";
  return "finishing";
}

export function rotatingLearnStatus(phase: LearnWaitPhase, tick: number): string {
  const lines = PHASE_LINES[phase];
  return lines[((tick % lines.length) + lines.length) % lines.length];
}
