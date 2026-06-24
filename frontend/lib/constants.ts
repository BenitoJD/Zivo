const GB_BYTES = 1024 * 1024 * 1024;
export const STORAGE_LIMIT_BYTES = GB_BYTES;
const MAX_UPLOAD_BYTES = GB_BYTES;

const INDEXING_STAGES = [
  { min: 0, title: "Starting", detail: "Setting up your study session" },
  { min: 10, title: "Reading", detail: "Pulling text from your source" },
  { min: 30, title: "Organizing", detail: "Structuring passages for search" },
  { min: 50, title: "Indexing", detail: "Making the material searchable" },
  { min: 80, title: "Almost ready", detail: "Finishing the context window" },
  { min: 100, title: "Ready", detail: "Opening study mode" },
] as const;

type IndexingStage = (typeof INDEXING_STAGES)[number];

export function indexingStage(progress: number): IndexingStage {
  let stage: IndexingStage = INDEXING_STAGES[0];
  for (const candidate of INDEXING_STAGES) {
    if (progress >= candidate.min) stage = candidate;
  }
  return stage;
}
