/** Rotating learn-mode wait copy — Claude Code-style active status lines. */

export type LearnWaitPhase = "indexing" | "planning" | "generating" | "finishing";

type StatusLine = string | ((page?: number) => string);

type PhaseCopy = {
  titles: StatusLine[];
  details: StatusLine[];
};

function pick<T>(items: T[], tick: number): T {
  if (items.length === 0) throw new Error("empty status pool");
  return items[((tick % items.length) + items.length) % items.length];
}

function render(line: StatusLine, page?: number): string {
  return typeof line === "function" ? line(page) : line;
}

const PHASE_COPY: Record<LearnWaitPhase, PhaseCopy> = {
  indexing: {
    titles: [
      "Reading your source",
      "Scanning selected pages",
      "Indexing passages",
      "Pulling text from the PDF",
    ],
    details: [
      "Staying scoped to the pages you picked.",
      "Making the source searchable for questions.",
      "Chunking text so retrieval stays on-topic.",
      "Skipping everything outside your range.",
    ],
  },
  planning: {
    titles: [
      (p) => (p ? `Mapping page ${p}` : "Mapping this page"),
      (p) => (p ? `Studying page ${p}` : "Studying this page"),
      "Finding what's testable",
      "Sketching the question plan",
      (p) => (p ? `Surveying page ${p}` : "Surveying the page"),
    ],
    details: [
      "Noting concepts worth a question.",
      "Separating core facts from background noise.",
      "Estimating how many questions this page can support.",
      "Marking ideas you should be able to recall.",
      "Deciding what to test before writing stems.",
    ],
  },
  generating: {
    titles: [
      "Writing questions",
      "Drafting your first batch",
      "Shaping multiple-choice stems",
      "Building answer choices",
      "Turning ideas into questions",
    ],
    details: [
      "A few at a time, as you work through the page.",
      "Calibrating difficulty to the source.",
      "Keeping correct answers anchored in the text.",
      "Writing distractors that actually compete.",
      "Queueing the next question while you study.",
    ],
  },
  finishing: {
    titles: ["Almost there", "One moment", "Final pass", "Wrapping up"],
    details: [
      "Your first question is about to appear.",
      "Loading the next item in the queue.",
      "Syncing the study queue.",
      "Ready in a second.",
    ],
  },
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

export function rotatingLearnStatus(
  phase: LearnWaitPhase,
  tick: number,
  page?: number,
): { title: string; detail: string } {
  const copy = PHASE_COPY[phase];
  return {
    title: render(pick(copy.titles, tick), page),
    detail: render(pick(copy.details, tick + 1), page),
  };
}

export function learnRangeLabel(pageFrom?: number, pageTo?: number, currentPage?: number): string | null {
  if (!pageFrom || !pageTo) return null;
  if (currentPage) return `Pages ${pageFrom}–${pageTo} · page ${currentPage}`;
  return `Pages ${pageFrom}–${pageTo}`;
}
