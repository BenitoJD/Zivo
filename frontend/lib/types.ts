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
  non_content?: boolean;
  no_questions_reason?: string | null;
  document_complete?: boolean;
  pool_available?: number;
  rag_window_pages?: number[];
  rag_window_ready?: boolean;
  /** Per-document selection mode: "adaptive" (difficulty_edge) or "classic" (sequence). */
  study_mode?: "adaptive" | "classic";
};

export type McqGradeResponse = {
  correct?: boolean;
  feedback?: string;
  correct_index?: number;
  /** Present only for multi-select ("select all that apply") items. */
  correct_indices?: number[];
};

export type AssertionPayload = {
  question?: string;
  stem?: string;
  options?: string[] | unknown;
  choices?: string[] | unknown;
  sequence?: number;
  primary_concept?: string;
  /** Present only for multi-select items — its length (≥2) marks the item multi. */
  correct_indices?: number[];
};

const OPTION_LETTER_PREFIX = /^(?:[A-Da-d]|[1-4])[.)]\s+/;
const LEADING_META_PATTERNS = [
  /^(?:(?:according|based)\s+to\s+(?:the\s+)?(?:page|text|passage|source|excerpt|reading|document|book|textbook|material)|from\s+(?:the\s+)?(?:page|text|passage|source|reading|document|book|textbook)|in\s+(?:the\s+)?(?:passage|text|excerpt|reading|document|book|textbook|material)|in\s+this\s+(?:book|text|reading|passage|document)|on\s+page\s+\d+|(?:the\s+)?(?:page|text|passage|source|reading|document|textbook)\s+(?:text\s+)?(?:specifies|states|says|indicates|describes|explains|mentions)(?:\s+that)?)[,:]?\s+/i,
  /^as\s+(?:the\s+)?(?:page|text|passage|reading|document)\s+(?:states|says)[,:]?\s+/i,
  /^the\s+text\s+states:\s*['"]?/i,
  /^as\s+(?:stated|described)\s+(?:in|above)[,:]?\s+/i,
  /^(?:on\s+page\s+\d+(?:\s+of\s+the\s+(?:text|book|passage))?)[,:]?\s+/i,
  /^(?:in\s+this\s+(?:book|text|reading|passage|document))[,:]?\s+/i,
];

function stripDocumentMeta(text: string): string {
  // Collapse horizontal whitespace but PRESERVE newlines — statement-based,
  // matching, ordering, and code stems carry meaningful line breaks the UI
  // renders (white-space: pre-line). Mirrors the backend sanitizer.
  let cleaned = String(text || "")
    .trim()
    .replace(/[^\S\n]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n");
  if (!cleaned) return "";
  for (let pass = 0; pass < 8; pass += 1) {
    let changed = false;
    for (const pattern of LEADING_META_PATTERNS) {
      const updated = cleaned.replace(pattern, "").trim();
      if (updated !== cleaned) {
        cleaned = updated;
        changed = true;
      }
    }
    if (!changed) break;
  }
  cleaned = cleaned.replace(/\s*The text states:.*$/i, "").trim();
  return cleaned;
}

function capitalizeFirst(text: string): string {
  if (text && text[0] === text[0].toLowerCase()) {
    return text[0].toUpperCase() + text.slice(1);
  }
  return text;
}

/** Strip leading A)/B. prefixes — the UI renders letter labels. */
function sanitizeMcqOption(text: string): string {
  return String(text || "")
    .trim()
    .replace(OPTION_LETTER_PREFIX, "")
    .trim();
}

/** Remove exam-forbidden framing so the stem stands alone like a formal test item. */
export function sanitizeMcqStem(text: string): string {
  return capitalizeFirst(stripDocumentMeta(text));
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
