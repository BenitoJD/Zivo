export type SourceDocument = {
  id: string;
  filename: string;
  content_type: string;
  status: string;
  index_progress: number;
  size_bytes: number;
  meta?: { source_type?: string; guest_id?: string };
};

export type ArtifactMeta = {
  id: string;
  status: string;
  meta?: { selected_range?: { from: number; to: number }; page_count?: number };
};

export type PagesInfo = {
  page_count: number;
  status: string;
};

export type McqState = {
  current_assertion_id: string | null;
  concepts: { concept_key: string; label: string }[];
  page_mastered: boolean;
  page_ready: boolean;
};

export type AssertionPayload = {
  question?: string;
  stem?: string;
  options?: string[];
  choices?: string[];
};
