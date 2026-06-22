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
  options?: string[] | unknown;
  choices?: string[] | unknown;
  sequence?: number;
};

const OPTION_LETTER_PREFIX = /^(?:[A-Da-d]|[1-4])[.)]\s+/;
const PAGE_REFERENCE_STEM =
  /^(?:(?:according|based)\s+to\s+(?:the\s+)?(?:page|text|passage|source|excerpt)|from\s+(?:the\s+)?(?:page|text|passage|source)|in\s+(?:the\s+)?(?:passage|text|excerpt)|(?:the\s+)?(?:page|text|passage|source)\s+(?:states|says|indicates|describes|explains)(?:\s+that)?)[,:]?\s+/i;

/** Strip leading A)/B. prefixes — the UI renders letter labels. */
export function sanitizeMcqOption(text: string): string {
  return String(text || "")
    .trim()
    .replace(OPTION_LETTER_PREFIX, "")
    .trim();
}

/** Remove meta framing like "According to the page," so the stem stands alone. */
export function sanitizeMcqStem(text: string): string {
  let cleaned = String(text || "").trim();
  while (PAGE_REFERENCE_STEM.test(cleaned)) {
    cleaned = cleaned.replace(PAGE_REFERENCE_STEM, "").trim();
  }
  if (cleaned && cleaned[0] === cleaned[0].toLowerCase()) {
    cleaned = cleaned[0].toUpperCase() + cleaned.slice(1);
  }
  return cleaned;
}

/** Coerce assertion payload options into a string array for the MCQ UI. */
export function normalizeMcqOptions(options?: unknown, choices?: unknown): string[] {
  const raw = options ?? choices;
  if (Array.isArray(raw)) {
    return raw.map((item) => sanitizeMcqOption(String(item))).filter((item) => item.length > 0);
  }
  if (raw && typeof raw === "object") {
    return Object.values(raw as Record<string, unknown>)
      .map((item) => sanitizeMcqOption(String(item)))
      .filter((item) => item.length > 0);
  }
  return [];
}
