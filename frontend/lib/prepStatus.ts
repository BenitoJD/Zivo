/** Background prep progress copy for the full-range cook flow. */

export type PrepProgress = {
  phase?: "indexing" | "cooking" | "complete";
  index_pct?: number;
  cook_pct?: number;
  overall_pct?: number;
};

export type PrepScreenStatus = {
  title: string;
  detail: string;
  phaseLabel: string;
};

const INDEXING_LINES = [
  { title: "Reading your pages", detail: "Indexing every page you selected" },
  { title: "Processing text", detail: "Extracting the ideas worth testing" },
] as const;

const COOKING_LINES = [
  { title: "Writing questions", detail: "Cooking MCQs for your whole study range" },
  { title: "Planning your quiz", detail: "Choosing what to test on each page" },
  { title: "Polishing wording", detail: "Making sure each question is clear and fair" },
] as const;

export function isBackgroundPrepActive(meta?: {
  prep_mode?: string;
  prep_complete?: boolean;
}): boolean {
  return meta?.prep_mode === "background" && !meta?.prep_complete;
}

export function prepScreenStatus(
  progress: PrepProgress | undefined,
  tick: number,
): PrepScreenStatus {
  const phase = progress?.phase === "cooking" ? "cooking" : "indexing";
  if (phase === "indexing") {
    const line = INDEXING_LINES[tick % INDEXING_LINES.length];
    return {
      title: line.title,
      detail: line.detail,
      phaseLabel: "Indexing",
    };
  }
  const line = COOKING_LINES[tick % COOKING_LINES.length];
  return {
    title: line.title,
    detail: line.detail,
    phaseLabel: "Cooking questions",
  };
}

export function prepProgressPercent(
  artifactStatus: string,
  progress: PrepProgress | undefined,
  indexProgress: number,
): number {
  if (progress?.overall_pct != null) return progress.overall_pct;
  if (artifactStatus === "prepping") return progress?.cook_pct ?? indexProgress;
  return progress?.index_pct ?? indexProgress;
}
