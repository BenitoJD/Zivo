export const GB_BYTES = 1024 * 1024 * 1024;
export const STORAGE_LIMIT_BYTES = GB_BYTES;
export const MAX_UPLOAD_BYTES = GB_BYTES;

export const INDEXING_STAGES = [
  { min: 0, title: "Starting", detail: "Queuing chat context for your first pages" },
  { min: 10, title: "Reading pages", detail: "Extracting text from the active study window" },
  { min: 30, title: "Building chunks", detail: "Organizing passages for tutor search" },
  { min: 50, title: "Embedding", detail: "Indexing only the pages needed for chat" },
  { min: 80, title: "Almost ready", detail: "Finishing the sliding context window" },
  { min: 100, title: "Finishing", detail: "Study mode opens next" },
] as const;

export type IndexingStage = (typeof INDEXING_STAGES)[number];

export function indexingStage(progress: number): IndexingStage {
  let stage: IndexingStage = INDEXING_STAGES[0];
  for (const candidate of INDEXING_STAGES) {
    if (progress >= candidate.min) stage = candidate;
  }
  return stage;
}
