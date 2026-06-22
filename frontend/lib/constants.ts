export const GB_BYTES = 1024 * 1024 * 1024;
export const STORAGE_LIMIT_BYTES = GB_BYTES;
export const MAX_UPLOAD_BYTES = GB_BYTES;

export const INDEXING_STAGES = [
  { min: 0, title: "Starting", detail: "Queuing your page range for processing" },
  { min: 10, title: "Reading file", detail: "Loading your document from storage" },
  { min: 30, title: "Extracting text", detail: "Parsing pages in your selected range" },
  { min: 50, title: "Building chunks", detail: "Organizing content for question generation" },
  { min: 80, title: "Indexing", detail: "Creating searchable embeddings" },
  { min: 100, title: "Finishing", detail: "Wrapping up — study mode opens next" },
] as const;

export type IndexingStage = (typeof INDEXING_STAGES)[number];

export function indexingStage(progress: number): IndexingStage {
  let stage: IndexingStage = INDEXING_STAGES[0];
  for (const candidate of INDEXING_STAGES) {
    if (progress >= candidate.min) stage = candidate;
  }
  return stage;
}
