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
  index_progress?: number;
  filename?: string;
  content_type?: string;
  meta?: { selected_range?: { from: number; to: number; pages?: number[] }; page_count?: number };
};

export type PagesInfo = {
  page_count: number;
  status: string;
  presigned_url?: string | null;
};

export type McqState = {
  current_assertion_id: string | null;
  current_page?: number;
  page_from?: number;
  page_to?: number;
  concepts: { concept_key: string; label: string }[];
  page_mastered: boolean;
  page_ready: boolean;
  generation_pending?: boolean;
  page_triage_complete?: boolean;
  generated_on_page?: number;
  answered_on_page?: number;
  max_per_page?: number;
  question_number?: number;
  question_budget?: number;
  questions_answered?: number;
  questions_generated?: number;
  coverage_complete?: boolean;
  page_complete?: boolean;
  document_complete?: boolean;
  pool_available?: number;
  rag_window_pages?: number[];
  rag_window_ready?: boolean;
};

export type McqGradeResponse = {
  correct?: boolean;
  feedback?: string;
  correct_index?: number;
};

export type AssertionPayload = {
  question?: string;
  stem?: string;
  options?: string[];
  choices?: string[];
  sequence?: number;
};
