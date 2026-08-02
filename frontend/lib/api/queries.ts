"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPatch, apiPost, apiPostBytes, setCsrfToken } from "@/lib/api/client";
import type { ArtifactMeta, PagesInfo, SourceDocument } from "@/lib/types";

export type AuthSession = {
  authenticated?: boolean;
  username: string | null;
  csrf_token: string | null;
  is_admin?: boolean;
};

export const queryKeys = {
  sources: ["sources"] as const,
  session: ["auth", "session"] as const,
  artifact: (id: string) => ["artifact", id] as const,
  artifactPages: (id: string) => ["artifact", id, "pages"] as const,
  assertion: (id: string) => ["assertion", id] as const,
  chatMessages: (artifactId: string, surface: string) => ["chat", artifactId, "messages", surface] as const,
  topics: (id: string) => ["topics", id] as const,
  topicExplain: (id: string, key: string) => ["topics", id, "explain", key] as const,
  notes: (id: string, kind: string) => ["notes", id, kind] as const,
  flashcards: (id: string) => ["flashcards", id] as const,
  memoryPalace: (id: string, setting: string) => ["memory-palace", id, setting] as const,
  savedNotes: (id: string) => ["saved-notes", id] as const,
  brainstormIdeas: (id: string) => ["brainstorm-ideas", id] as const,
  quiz: (id: string, config: string) => ["quiz", id, config] as const,
  studyReport: (id: string) => ["study-report", id] as const,
  progress: (artifactId?: string | null) =>
    artifactId ? (["progress", artifactId] as const) : (["progress", "lifetime"] as const),
  interview: (id: string) => ["interview", id] as const,
  mains: (id: string) => ["mains", id] as const,
  resume: (id: string) => ["resume", id] as const,
  codingWorkspace: (id: string) => ["coding", "workspace", id] as const,
  codingProblem: (id: string) => ["coding", "problem", id] as const,
  codingPublic: (filters?: string) =>
    filters ? (["coding", "public", filters] as const) : (["coding", "public"] as const),
  codingLanguages: () => ["coding", "languages"] as const,
  codingAdmin: () => ["coding", "admin"] as const,
  codingAdminProblem: (id: string) => ["coding", "admin", id] as const,
  debugPublic: (filters?: string) =>
    filters ? (["debug", "public", filters] as const) : (["debug", "public"] as const),
  debugScenario: (id: string) => ["debug", "scenario", id] as const,
  debugCookJob: (id: string) => ["debug", "cook", id] as const,
  debugAdmin: () => ["debug", "admin"] as const,
  debugAdminScenario: (id: string) => ["debug", "admin", id] as const,
  systemDesignPath: () => ["system-design", "path"] as const,
  systemDesignRecommended: () => ["system-design", "recommended"] as const,
  systemDesignProblem: (id: string) => ["system-design", "problem", id] as const,
  systemDesignSession: (id: string) => ["system-design", "session", id] as const,
  newspaperCatalog: () => ["newspaper", "catalog"] as const,
  newspaperDays: (slug: string) => ["newspaper", "days", slug] as const,
  newspaperQuestions: (id: string) => ["newspaper", "questions", id] as const,
  newspaperChannel: () => ["newspaper", "channel"] as const,
  newspaperBrands: () => ["newspaper", "brands"] as const,
  seoLearnSettings: () => ["seo-learn", "settings"] as const,
  seoLearnAdminPosts: () => ["seo-learn", "admin-posts"] as const,
};

export type StudyReport = {
  total: number;
  correct: number;
  wrong: number;
  topics: { concept: string; correct: number; total: number }[];
};

/** Persistent end-of-study report (first-attempt accuracy per concept), aggregated
 *  server-side from immutable answer measurements - survives reloads. */
export function useStudyReportQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.studyReport(artifactId),
    queryFn: () => apiGet<StudyReport>(`/api/artifacts/${artifactId}/report`),
    enabled: enabled && Boolean(artifactId),
    staleTime: 30_000,
  });
}

export type LearnerProgress = {
  first_attempt_only: boolean;
  answers: {
    total: number;
    correct: number;
    wrong: number;
    accuracy: number | null;
  };
  today: { answered: number; correct: number; questions_asked: number };
  questions_asked: number;
  recent: { day: string; answered: number; correct: number; questions_asked: number }[];
  sources: {
    artifact_id: string;
    title: string;
    total: number;
    correct: number;
    wrong: number;
    questions_asked: number;
    last_answered_at: string | null;
  }[];
  topics: { concept: string; correct: number; total: number }[];
  scoped_artifact_id: string | null;
};

/** Lifetime (or per-source) learning journal from measurements + tutor chat. */
export function useLearnerProgressQuery(artifactId?: string | null, enabled = true) {
  const scoped = artifactId || null;
  return useQuery({
    queryKey: queryKeys.progress(scoped),
    queryFn: () =>
      apiGet<LearnerProgress>(
        scoped ? `/api/progress?artifact_id=${encodeURIComponent(scoped)}` : "/api/progress",
      ),
    enabled,
    staleTime: 30_000,
  });
}

export type Topic = { key: string; title: string; summary: string };
export type TopicsResponse = {
  status: "indexing" | "generating" | "ready" | "failed" | "missing";
  topics: Topic[];
};
export type ExplainResponse = {
  status: string;
  topic_key: string;
  explanation: string | null;
};

export function useTopicsQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.topics(artifactId),
    queryFn: () => apiGet<TopicsResponse>(`/api/artifacts/${artifactId}/topics`),
    enabled: enabled && Boolean(artifactId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "ready" || status === "failed" ? false : 3000;
    },
  });
}

export type NoteKind = "notes" | "cheatsheet";
export type NotesResponse = {
  status: "indexing" | "generating" | "ready" | "failed" | "missing";
  kind: string;
  content: string;
};
export type Flashcard = { front: string; back: string; kind: "qa" | "cloze" };
export type FlashcardsResponse = {
  status: "indexing" | "generating" | "ready" | "failed" | "missing";
  cards: Flashcard[];
};

const stillBuilding = (status?: string) =>
  status === "indexing" || status === "generating" || status === "missing" || !status;

/** Poll every `ms` while a status-bearing query is still building, else stop. */
const pollWhileBuilding = (ms = 3000) => (query: { state: { data?: { status?: string } } }) =>
  stillBuilding(query.state.data?.status) ? ms : false;

/** Poll every `ms` only while a query is in the "indexing" status, else stop. */
const pollWhileIndexing = (ms = 3000) => (query: { state: { data?: { status?: string } } }) =>
  query.state.data?.status === "indexing" ? ms : false;

/** Mains: poll only while a question is generating or an answer is being graded.
 * Stops at "awaiting_answer" (user's turn), "ready", "failed", and "missing" (setup). */
const pollWhileMainsBusy = (ms = 2500) => (query: { state: { data?: { status?: string } } }) => {
  const s = query.state.data?.status;
  return s === "generating" || s === "grading" || s === "indexing" ? ms : false;
};

// NOTE: poll callbacks must NEVER gate the interval on `document.visibilityState`.
// Returning `false` tells React Query to STOP the timer (not pause it); with the
// global `refetchOnWindowFocus: false`, nothing re-triggers a fetch when the tab
// regains focus, so polling wedges and the result only appears after a manual
// refresh (the "stuck at 40%" bug). React Query already pauses interval refetches
// in the background and resumes them on focus - let it. Just return the interval
// while work is pending.

export function useNotesQuery(artifactId: string, kind: NoteKind = "notes", enabled = true) {
  return useQuery({
    queryKey: queryKeys.notes(artifactId, kind),
    queryFn: () => apiGet<NotesResponse>(`/api/artifacts/${artifactId}/notes?kind=${kind}`),
    enabled: enabled && Boolean(artifactId),
    refetchInterval: pollWhileBuilding(),
  });
}

export function useFlashcardsQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.flashcards(artifactId),
    queryFn: () => apiGet<FlashcardsResponse>(`/api/artifacts/${artifactId}/flashcards`),
    enabled: enabled && Boolean(artifactId),
    refetchInterval: pollWhileBuilding(),
  });
}

export type PalaceStation = {
  key: string;
  locus: string;
  term: string;
  fact: string;
  image: string;
  cue: string;
};
export type MemoryPalace = { setting: string; intro: string; stations: PalaceStation[] };
export type MemoryPalaceResponse = {
  status: "indexing" | "generating" | "ready" | "failed" | "missing";
  setting: string;
  palace: MemoryPalace | null;
};

export function useMemoryPalaceQuery(artifactId: string, setting = "", enabled = true) {
  const qs = setting ? `?setting=${encodeURIComponent(setting)}` : "";
  return useQuery({
    queryKey: queryKeys.memoryPalace(artifactId, setting),
    queryFn: () => apiGet<MemoryPalaceResponse>(`/api/artifacts/${artifactId}/memory-palace${qs}`),
    enabled: enabled && Boolean(artifactId),
    refetchInterval: pollWhileBuilding(),
  });
}

export type SavedNote = { id: string; content: string; quote: string | null; created_at: string | null };

export function useSavedNotesQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.savedNotes(artifactId),
    queryFn: () => apiGet<{ notes: SavedNote[] }>(`/api/artifacts/${artifactId}/saved-notes`),
    enabled: enabled && Boolean(artifactId),
    staleTime: 10_000,
  });
}

/** Saved-notes mutations (save / delete) with cache invalidation. */
export function useSavedNotesActions(artifactId: string) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: queryKeys.savedNotes(artifactId) });
  return {
    save: async (content: string, quote?: string | null) => {
      const note = await apiPost<SavedNote>(`/api/artifacts/${artifactId}/saved-notes`, {
        content,
        quote: quote ?? null,
      });
      void invalidate();
      return note;
    },
    remove: async (noteId: string) => {
      await apiDelete(`/api/artifacts/${artifactId}/saved-notes/${noteId}`);
      void invalidate();
    },
  };
}

/** One kept brainstorm idea. `children` are ideas branched off it (the mind-map edge). */
export type BrainstormIdea = {
  id: string;
  parent_id: string | null;
  text: string;
  angle: string;
  created_at: string | null;
  children: BrainstormIdea[];
};

/** The kept-idea tree. The mind map draws it; the board flattens it. */
export function useBrainstormIdeasQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.brainstormIdeas(artifactId),
    queryFn: () =>
      apiGet<{ tree: BrainstormIdea[] }>(`/api/artifacts/${artifactId}/brainstorm-ideas`),
    enabled: enabled && Boolean(artifactId),
    staleTime: 10_000,
  });
}

/** Flatten the idea tree to a newest-first list — the board ordering. */
export function flattenIdeas(tree: BrainstormIdea[]): BrainstormIdea[] {
  const out: BrainstormIdea[] = [];
  const walk = (nodes: BrainstormIdea[]) => {
    for (const n of nodes) {
      out.push(n);
      walk(n.children ?? []);
    }
  };
  walk(tree);
  return out.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
}

/** Brainstorm idea mutations (keep / delete) with cache invalidation. */
export function useBrainstormActions(artifactId: string) {
  const qc = useQueryClient();
  const invalidate = () =>
    qc.invalidateQueries({ queryKey: queryKeys.brainstormIdeas(artifactId) });
  return {
    keep: async (text: string, angle = "", parentId: string | null = null) => {
      const idea = await apiPost<BrainstormIdea>(
        `/api/artifacts/${artifactId}/brainstorm-ideas`,
        { text, angle, parent_id: parentId },
      );
      void invalidate();
      return idea;
    },
    remove: async (ideaId: string) => {
      await apiDelete(`/api/artifacts/${artifactId}/brainstorm-ideas/${ideaId}`);
      void invalidate();
    },
  };
}

export type QuizQuestion = {
  // mcq_negative / assertion_reason / scenario / cloze are single-best-answer MCQ
  // variants - same payload shape as "mcq" (options + answer_index).
  type:
    | "mcq"
    | "multi"
    | "mcq_negative"
    | "assertion_reason"
    | "scenario"
    | "cloze"
    | "truefalse"
    | "fill_blank"
    | "short"
    | "essay"
    | "matching";
  prompt: string;
  explanation?: string;
  options?: string[];
  answer_index?: number;
  answer_indices?: number[];
  answer?: string | boolean;
  pairs?: { left: string; right: string }[];
};
export type QuizConfig = { types: string[]; count: number; difficulty: string };
export type QuizResponse = {
  status: "indexing" | "generating" | "ready" | "failed" | "missing";
  config: string;
  questions: QuizQuestion[];
};

export function useQuizQuery(artifactId: string, cfg: QuizConfig, enabled = true) {
  const qs = `types=${cfg.types.join(",")}&count=${cfg.count}&difficulty=${cfg.difficulty}`;
  return useQuery({
    queryKey: queryKeys.quiz(artifactId, qs),
    queryFn: () => apiGet<QuizResponse>(`/api/artifacts/${artifactId}/quiz?${qs}`),
    enabled: enabled && Boolean(artifactId) && cfg.types.length > 0,
    refetchInterval: pollWhileBuilding(),
  });
}

// -------------------------------------------------------------- interview mode
export type InterviewCategoryMeta = { label: string; blurb: string };
export type InterviewQuestion = {
  kind: "mcq" | "typed" | "coding";
  round_name: string;
  question: string;
  options?: string[];
  // coding
  starter_code?: string;
  language_id?: number;
  test_count?: number;
};
export type InterviewScores = { problem_framing: number; depth: number; tradeoffs: number; communication: number };
export type InterviewTurn = {
  round_name: string;
  kind: "mcq" | "typed" | "coding";
  question: string;
  feedback: string;
  // mcq
  options?: string[];
  selected_index?: number;
  correct_index?: number;
  correct?: boolean;
  // typed
  answer?: string;
  scores?: InterviewScores;
  // coding
  passed?: number;
  total?: number;
  language_id?: number;
};
export type InterviewLanguage = { id: number; label: string };
export type CodeRunResult = {
  status: string;
  stdout: string;
  stderr: string;
  compile_output: string;
  time?: string | null;
  memory?: number | null;
};
export type CodingAnswer = { source: string; language_id: number };
export type InterviewReport = {
  overall: number;
  rounds: { name: string; kind: "mcq" | "typed"; score: number; detail: string }[];
  strengths: string[];
  focus_areas: string[];
};
export type InterviewState = {
  status: "missing" | "indexing" | "in_progress" | "complete" | "failed";
  category: string;
  categories: Record<string, InterviewCategoryMeta>;
  rounds: { name: string; kind: "mcq" | "typed"; questions: number }[];
  round_index: number;
  total_rounds: number;
  current_question: InterviewQuestion | null;
  transcript: InterviewTurn[];
  report?: InterviewReport;
  error?: string | null;
};

export function useInterviewQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.interview(artifactId),
    queryFn: () => apiGet<InterviewState>(`/api/artifacts/${artifactId}/interview`),
    enabled: enabled && Boolean(artifactId),
    refetchInterval: pollWhileIndexing(),
  });
}

/** start / answer / reset all return the full new state - write it straight into the cache. */
export function useInterviewActions(artifactId: string) {
  const qc = useQueryClient();
  const put = (state: InterviewState) => qc.setQueryData(queryKeys.interview(artifactId), state);
  return {
    start: async (category: string) =>
      put(await apiPost<InterviewState>(`/api/artifacts/${artifactId}/interview/start`, { category })),
    answer: async (answer: string | number | CodingAnswer) =>
      put(await apiPost<InterviewState>(`/api/artifacts/${artifactId}/interview/answer`, { answer })),
    reset: async () =>
      put(await apiPost<InterviewState>(`/api/artifacts/${artifactId}/interview/reset`, {})),
    runCode: (source: string, language_id: number, stdin = "") =>
      apiPost<CodeRunResult>(`/api/artifacts/${artifactId}/interview/run-code`, { source, language_id, stdin }),
  };
}

export function useInterviewLanguagesQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: ["interview", artifactId, "languages"] as const,
    queryFn: () => apiGet<{ languages: InterviewLanguage[] }>(`/api/artifacts/${artifactId}/interview/languages`),
    enabled: enabled && Boolean(artifactId),
    staleTime: Infinity,
  });
}

// -------------------------------------------------------------- mains mode
export type MainsStrictness = "exam" | "coaching" | "gentle";
export type MainsAxis = { key: string; label: string; score: number; max: number; comment: string };
export type MainsSchemeHit = { point: string; marks: number; hit: boolean };
export type MainsHighlight = { quote: string; kind: "strong" | "weak" | "error"; comment: string };
export type MainsResult = {
  marks: number;
  marks_max: number;
  band: string;
  axes: MainsAxis[];
  scheme_hits: MainsSchemeHit[];
  keep_doing: string[];
  improve: string[];
  examiner_note: string;
  highlights: MainsHighlight[];
};
export type MainsState = {
  status: "missing" | "indexing" | "generating" | "awaiting_answer" | "grading" | "ready" | "failed";
  question: string;
  directive: string;
  marks_max: number;
  strictness: MainsStrictness;
  input_kind?: "typed" | "handwritten" | null;
  answer?: string;
  result?: MainsResult | null;
  error?: string | null;
};

export function useMainsQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.mains(artifactId),
    queryFn: () => apiGet<MainsState>(`/api/artifacts/${artifactId}/mains`),
    enabled: enabled && Boolean(artifactId),
    refetchInterval: pollWhileMainsBusy(),
  });
}

/** start (new question) + answer both return the fresh state - write it into the cache;
 * the poll then flips generating/grading → awaiting_answer/ready. */
export function useMainsActions(artifactId: string) {
  const qc = useQueryClient();
  const put = (state: MainsState) => qc.setQueryData(queryKeys.mains(artifactId), state);
  return {
    start: async (strictness: MainsStrictness, marks_max: number) =>
      put(await apiPost<MainsState>(`/api/artifacts/${artifactId}/mains/start`, { strictness, marks_max })),
    answer: async (payload: { text?: string; image_document_id?: string }) =>
      put(await apiPost<MainsState>(`/api/artifacts/${artifactId}/mains/answer`, payload)),
  };
}

// -------------------------------------------------------------- coding practice
// LeetCode-style problem bank. Workspace view (authed, persistent status) +
// public sampler (anonymous) share the same problem shape and the same
// run/submit endpoints; only the list source differs.
export type CodingTestCase = { stdin: string; expected_output: string };
export type CodingProblemStatus = "new" | "solved";
export type CodingProblemListItem = {
  id: string;
  title: string;
  difficulty: "easy" | "medium" | "hard";
  language_id: number;
  sample_test_count: number;
  hidden_test_count: number;
  page_number?: number;
  status?: CodingProblemStatus;
  tags?: string[];
  origin?: "generated" | "curated";
  concept?: string;
  published?: boolean;
};
export type CodingProblem = {
  id: string;
  format: string;
  title: string;
  statement: string;
  starter_code: string;
  language_id: number;
  language_label: string;
  difficulty: "easy" | "medium" | "hard";
  sample_tests: CodingTestCase[];
  test_count: number;
  concept: string;
  tags: string[];
  origin?: "generated" | "curated";
  status?: CodingProblemStatus;
  published?: boolean;
};
export type CodingEditorialProblem = CodingProblem & {
  hidden_tests: CodingTestCase[];
  editor_solution: string;
};
export type CodingSubmitResult = {
  passed: number;
  total: number;
  all_passed: boolean;
  cases: { ok: boolean; stdin?: string; expected?: string; stdout?: string; stderr?: string }[];
  error: string | null;
  status: CodingProblemStatus;
  mentor_summary?: string;
  weak_concepts?: string[];
  lesson?: { title: string; body: string; try_this: string };
  recommended_next_id?: string | null;
  reference_solution?: string | null;
};

export type CodingPublicFilters = {
  difficulty?: "easy" | "medium" | "hard";
  tag?: string;
  status?: "new" | "solved";
  origin?: "generated" | "curated";
};

export function useCodingWorkspaceQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.codingWorkspace(artifactId),
    queryFn: () => apiGet<{ artifact_id: string; items: CodingProblemListItem[] }>(`/api/coding/workspace/${artifactId}`),
    enabled: enabled && Boolean(artifactId),
    refetchInterval: (query) => {
      // Poll while problems are still being generated (page triage → coding job).
      const n = query.state.data?.items.length ?? 0;
      return n === 0 ? 4000 : false;
    },
  });
}

export function useCodingProblemQuery(id: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.codingProblem(id ?? ""),
    queryFn: () => apiGet<CodingProblem>(`/api/coding/${id}`),
    enabled: enabled && Boolean(id),
  });
}

export function useCodingPublicQuery(filters: CodingPublicFilters = {}) {
  const qs = new URLSearchParams();
  if (filters.difficulty) qs.set("difficulty", filters.difficulty);
  if (filters.tag) qs.set("tag", filters.tag);
  if (filters.status) qs.set("status", filters.status);
  if (filters.origin) qs.set("origin", filters.origin);
  const q = qs.toString();
  return useQuery({
    queryKey: queryKeys.codingPublic(q || undefined),
    queryFn: () =>
      apiGet<{ items: CodingProblemListItem[]; count: number; tags: string[] }>(
        `/api/coding${q ? `?${q}` : ""}`,
      ),
  });
}

export function useCodingLanguagesQuery() {
  return useQuery({
    queryKey: queryKeys.codingLanguages(),
    queryFn: () => apiGet<{ languages: InterviewLanguage[] }>(`/api/coding/meta/languages`),
    staleTime: Infinity,
  });
}

export function useCodingAdminQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.codingAdmin(),
    queryFn: () => apiGet<{ items: CodingProblemListItem[]; count: number }>(`/api/coding/admin`),
    enabled,
    retry: false,
  });
}

export function useCodingAdminProblemQuery(id: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.codingAdminProblem(id ?? ""),
    queryFn: () => apiGet<CodingEditorialProblem>(`/api/coding/admin/${id}`),
    enabled: enabled && Boolean(id),
    retry: false,
  });
}

/** Run + submit actions for one problem. Both invalidate the problem + workspace
 *  caches so per-problem status refreshes after a submit. */
export function useCodingActions(assertionId: string) {
  const qc = useQueryClient();
  return {
    runCode: (source: string, language_id: number, stdin = "") =>
      apiPost<CodeRunResult>(`/api/coding/${assertionId}/run`, { source, language_id, stdin }),
    submit: async (source: string, language_id: number) => {
      const result = await apiPost<CodingSubmitResult>(`/api/coding/${assertionId}/submit`, { source, language_id });
      qc.invalidateQueries({ queryKey: queryKeys.codingProblem(assertionId) });
      qc.invalidateQueries({ queryKey: ["coding", "public"] });
      return result;
    },
    invalidateWorkspace: (artifactId: string) => {
      qc.invalidateQueries({ queryKey: queryKeys.codingWorkspace(artifactId) });
    },
  };
}

export function useCodingCurateActions() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: queryKeys.codingAdmin() });
    qc.invalidateQueries({ queryKey: ["coding", "public"] });
  };
  return {
    seed: () => apiPost<{ created: number; updated: number; total: number }>("/api/coding/admin/seed", {}),
    create: (body: Record<string, unknown>) => apiPost<CodingProblem>("/api/coding/admin", body),
    update: (id: string, body: Record<string, unknown>) =>
      apiPatch<CodingProblem>(`/api/coding/admin/${id}`, body),
    remove: (id: string) => apiDelete(`/api/coding/admin/${id}`),
    invalidate,
  };
}

// -------------------------------------------------------------- debug diagnostics
export type DebugArtifact = {
  kind: string;
  language?: string;
  label?: string;
  content: string;
};
export type DebugStep = {
  key: string;
  question: string;
  options: string[];
};
export type DebugScenarioListItem = {
  id: string;
  title: string;
  scenario_type: string;
  difficulty: "easy" | "medium" | "hard";
  step_count: number;
  tags?: string[];
  origin?: string;
  published?: boolean;
  review_status?: string;
};
export type DebugScenario = {
  id: string;
  format: string;
  title: string;
  scenario_type: string;
  difficulty: string;
  tags: string[];
  origin?: string;
  case: { summary?: string; artifacts?: DebugArtifact[] };
  steps: DebugStep[];
  step_count: number;
};
export type DebugGradeStepResult = {
  correct: boolean;
  correct_index: number;
  explanation: string;
  step_key: string;
};
export type DebugCookJob = {
  id: string;
  status: string;
  review_status: string;
  scenario_ids: string[];
  material_preview?: string;
  brief?: string;
  error?: string | null;
};

export type DebugPublicFilters = {
  difficulty?: "easy" | "medium" | "hard";
  scenario_type?: string;
  tag?: string;
};

export function useDebugPublicQuery(filters: DebugPublicFilters = {}) {
  const qs = new URLSearchParams();
  if (filters.difficulty) qs.set("difficulty", filters.difficulty);
  if (filters.scenario_type) qs.set("scenario_type", filters.scenario_type);
  if (filters.tag) qs.set("tag", filters.tag);
  const q = qs.toString();
  return useQuery({
    queryKey: queryKeys.debugPublic(q || undefined),
    queryFn: () =>
      apiGet<{ items: DebugScenarioListItem[]; limit: number; offset: number }>(
        `/api/debug${q ? `?${q}` : ""}`,
      ),
  });
}

export function useDebugScenarioQuery(id: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.debugScenario(id ?? ""),
    queryFn: () => apiGet<DebugScenario>(`/api/debug/${id}`),
    enabled: enabled && Boolean(id),
  });
}

export function useDebugCookJobQuery(id: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.debugCookJob(id ?? ""),
    queryFn: () => apiGet<DebugCookJob>(`/api/debug/cook/${id}`),
    enabled: enabled && Boolean(id),
    refetchInterval: (query) => {
      const st = query.state.data?.status;
      return st === "queued" || st === "cooking" ? 3000 : false;
    },
  });
}

export function useDebugAdminQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.debugAdmin(),
    queryFn: () => apiGet<{ items: DebugScenarioListItem[] }>(`/api/debug/admin/list`),
    enabled,
    retry: false,
  });
}

export function useDebugActions() {
  const qc = useQueryClient();
  return {
    startCook: (body: { material: string; brief?: string; scenario_count?: number }) =>
      apiPost<DebugCookJob>("/api/debug/cook", body),
    submitToLibrary: (jobId: string) =>
      apiPost<DebugCookJob>(`/api/debug/cook/${jobId}/submit-to-library`, {}),
    gradeStep: (id: string, body: { step_key: string; choice_index: number; steps_correct?: number; steps_total?: number }) =>
      apiPost<DebugGradeStepResult>(`/api/debug/${id}/grade-step`, body),
    invalidatePublic: () => qc.invalidateQueries({ queryKey: ["debug", "public"] }),
    invalidateCook: (jobId: string) =>
      qc.invalidateQueries({ queryKey: queryKeys.debugCookJob(jobId) }),
  };
}

export function useDebugCurateActions() {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: queryKeys.debugAdmin() });
    qc.invalidateQueries({ queryKey: ["debug", "public"] });
  };
  return {
    seed: () => apiPost<{ created: number; updated: number; total: number }>("/api/debug/admin/seed", {}),
    create: (body: Record<string, unknown>) => apiPost<DebugScenario>("/api/debug/admin", body),
    update: (id: string, body: Record<string, unknown>) =>
      apiPatch<DebugScenario>(`/api/debug/admin/${id}`, body),
    review: (id: string, body: { review_status: string; published?: boolean }) =>
      apiPatch<{ id: string; review_status: string }>(`/api/debug/admin/${id}/review`, body),
    remove: (id: string) => apiDelete(`/api/debug/admin/${id}`),
    invalidate,
  };
}

// -------------------------------------------------------------- system design mastery
export type SdConceptState = "not_started" | "in_progress" | "needs_work" | "strong";
export type SdPathConcept = {
  key: string;
  title: string;
  blurb: string;
  prerequisites: string[];
  state: SdConceptState;
  mastery: number | null;
};
export type SdPath = {
  concepts: SdPathConcept[];
  focus_key: string | null;
  focus_title: string;
};
export type SdDesign = {
  requirements: string;
  apis: string;
  data: string;
  scale: string;
  blocks: string[];
};
export type SdLesson = {
  title: string;
  body: string;
  try_this: string;
};
export type SdDimension = {
  key: string;
  score: number;
  note: string;
};
export type SdProblem = {
  id: string;
  slug: string;
  title: string;
  prompt: string;
  constraints: string;
  difficulty: "easy" | "medium" | "hard";
  concept_keys: string[];
  sort_order?: number;
  reference_design?: string;
};
export type SdSession = {
  id: string;
  problem_id: string;
  status: "active" | "done";
  design: Partial<SdDesign>;
  scores: { dimensions?: SdDimension[] };
  feedback: { mentor_summary?: string };
  weak_concepts: string[];
  lesson: Partial<SdLesson>;
  recommended_next_id: string | null;
  problem: SdProblem | null;
  building_blocks: string[];
  reference_design?: string;
};
export type SdRecommended = {
  problem: SdProblem | null;
  active_session: SdSession | null;
  focus_key: string | null;
  focus_title: string;
  building_blocks: string[];
};

export function useSystemDesignPathQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.systemDesignPath(),
    queryFn: () => apiGet<SdPath>("/api/system-design/path"),
    enabled,
  });
}

export function useSystemDesignRecommendedQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.systemDesignRecommended(),
    queryFn: () => apiGet<SdRecommended>("/api/system-design/recommended"),
    enabled,
  });
}

export function useSystemDesignProblemQuery(id: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.systemDesignProblem(id ?? ""),
    queryFn: () => apiGet<SdProblem>(`/api/system-design/problems/${id}`),
    enabled: enabled && Boolean(id),
  });
}

export function useSystemDesignSessionQuery(id: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.systemDesignSession(id ?? ""),
    queryFn: () => apiGet<SdSession>(`/api/system-design/sessions/${id}`),
    enabled: enabled && Boolean(id),
  });
}

export function useSystemDesignActions() {
  const qc = useQueryClient();
  const invalidateDoor = () => {
    void qc.invalidateQueries({ queryKey: queryKeys.systemDesignPath() });
    void qc.invalidateQueries({ queryKey: queryKeys.systemDesignRecommended() });
  };
  return {
    startSession: async (problem_id: string) => {
      const sess = await apiPost<SdSession>("/api/system-design/sessions", { problem_id });
      invalidateDoor();
      qc.setQueryData(queryKeys.systemDesignSession(sess.id), sess);
      return sess;
    },
    saveDesign: async (sessionId: string, design: SdDesign) => {
      const sess = await apiPost<SdSession>(`/api/system-design/sessions/${sessionId}/design`, design);
      qc.setQueryData(queryKeys.systemDesignSession(sessionId), sess);
      return sess;
    },
    submit: async (sessionId: string, design: SdDesign) => {
      const sess = await apiPost<SdSession>(`/api/system-design/sessions/${sessionId}/submit`, design);
      qc.setQueryData(queryKeys.systemDesignSession(sessionId), sess);
      invalidateDoor();
      return sess;
    },
    invalidateDoor,
  };
}

export type NewspaperPaper = {
  slug: string;
  title: string;
  ready_days: number;
  latest_date: string | null;
};
export type NewspaperCatalog = {
  retention_days: number;
  since: string;
  papers: NewspaperPaper[];
};
export type NewspaperDayLearner = {
  questions_answered: number;
  questions_total: number;
  learn_complete: boolean;
  in_progress: boolean;
};
export type NewspaperDay = {
  id: string;
  edition_date: string;
  status: string;
  document_id: string | null;
  has_blog?: boolean;
  blog_href?: string | null;
  learner?: NewspaperDayLearner;
};
export type NewspaperDays = {
  paper_slug: string;
  paper_title: string;
  since: string;
  days: NewspaperDay[];
};
export type NewspaperQuestion = {
  id: string;
  question: string;
  options: string[];
  is_multi?: boolean;
};
export type NewspaperQuestions = {
  edition: {
    id: string;
    paper_slug: string;
    paper_title: string;
    edition_date: string;
    status: string;
    document_id: string | null;
  } | null;
  items: NewspaperQuestion[];
};
export type NewspaperChannel = {
  channel_ref: string;
  channel_label: string;
  sync_cursor: number | null;
  allowlist_only?: boolean;
  updated_at: string | null;
};
export type NewspaperBrand = {
  slug: string;
  title: string;
  enabled: boolean;
  first_seen_at: string | null;
};
export type NewspaperBrands = {
  allowlist_only: boolean;
  brands: NewspaperBrand[];
};

export function useNewspaperCatalogQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.newspaperCatalog(),
    queryFn: () => apiGet<NewspaperCatalog>("/api/newspaper/catalog"),
    enabled,
  });
}

export function useNewspaperDaysQuery(slug: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.newspaperDays(slug ?? ""),
    queryFn: () => apiGet<NewspaperDays>(`/api/newspaper/papers/${slug}/days`),
    enabled: enabled && Boolean(slug),
  });
}

export function useNewspaperQuestionsQuery(editionId: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: queryKeys.newspaperQuestions(editionId ?? ""),
    queryFn: () => apiGet<NewspaperQuestions>(`/api/newspaper/editions/${editionId}/questions`),
    enabled: enabled && Boolean(editionId),
    refetchInterval: (q) => {
      const status = q.state.data?.edition?.status;
      return status === "indexing" || status === "pending" ? 4000 : false;
    },
  });
}

export function useNewspaperChannelQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.newspaperChannel(),
    queryFn: () => apiGet<NewspaperChannel>("/api/newspaper/admin/channel"),
    enabled,
    retry: false,
  });
}

export function useNewspaperBrandsQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.newspaperBrands(),
    queryFn: () => apiGet<NewspaperBrands>("/api/newspaper/admin/brands"),
    enabled,
    retry: false,
  });
}

export type SeoLearnSettings = {
  cook_enabled: boolean;
  soft_max_per_day: number;
  updated_at: string | null;
};

export type SeoLearnAdminPost = {
  id: string;
  slug: string;
  title: string;
  stream: string;
  status: string;
  author_name: string;
  source_kind: string;
  published_at: string | null;
  created_at: string | null;
};

export function useSeoLearnSettingsQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.seoLearnSettings(),
    queryFn: () => apiGet<SeoLearnSettings>("/api/learn/admin/settings"),
    enabled,
    retry: false,
  });
}

export function useSeoLearnAdminPostsQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.seoLearnAdminPosts(),
    queryFn: () => apiGet<{ items: SeoLearnAdminPost[] }>("/api/learn/admin/posts"),
    enabled,
    retry: false,
  });
}

// -------------------------------------------------------------- resume suite
export type ResumeCheck = { name: string; pass: boolean; detail: string };
export type ResumeJob = { role: string; company: string; dates: string; bullets: string[] };
export type ResumeEducation = { degree: string; school: string; dates: string };
export type ResumeStructured = {
  name?: string; title?: string; email?: string; phone?: string; location?: string;
  links?: string[]; summary?: string; experience?: ResumeJob[]; education?: ResumeEducation[]; skills?: string[];
};
export type ResumeAnalysis = {
  score: number; det_score: number; content_score: number;
  checks: ResumeCheck[]; strengths: string[]; improvements: string[]; structured: ResumeStructured;
};
export type ResumeState = {
  status: "missing" | "indexing" | "pending" | "ready" | "failed";
  analysis: Partial<ResumeAnalysis>;
  review_requested: boolean;
  error?: string | null;
};
export type OptimizeResult = {
  summary: string;
  bullets: { original: string; improved: string }[];
  missing_keywords: string[];
  notes: string;
};

export function useResumeAtsQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.resume(artifactId),
    queryFn: () => apiGet<ResumeState>(`/api/artifacts/${artifactId}/resume/ats`),
    enabled: enabled && Boolean(artifactId),
    // Resume ATS returns "indexing" while the doc isn't ready yet, then "pending"
    // briefly before "ready"/"failed". Poll through both busy states.
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "indexing" || s === "pending" ? 3000 : false;
    },
  });
}

export function useResumeActions(artifactId: string) {
  const qc = useQueryClient();
  return {
    optimize: (job_description: string) =>
      apiPost<OptimizeResult>(`/api/artifacts/${artifactId}/resume/optimize`, { job_description }),
    requestReview: async () => {
      const s = await apiPost<ResumeState>(`/api/artifacts/${artifactId}/resume/request-review`, {});
      qc.setQueryData(queryKeys.resume(artifactId), s);
      return s;
    },
    buildDocx: (data: ResumeStructured, template: "ats" | "modern") =>
      apiPostBytes(`/api/artifacts/${artifactId}/resume/build.docx`, { data, template }),
  };
}

export function useTopicExplanationQuery(artifactId: string, topicKey: string | null) {
  return useQuery({
    queryKey: topicKey
      ? queryKeys.topicExplain(artifactId, topicKey)
      : (["topics", "explain", "none"] as const),
    queryFn: () =>
      apiGet<ExplainResponse>(`/api/artifacts/${artifactId}/topics/${topicKey}/explain`),
    enabled: Boolean(artifactId && topicKey),
    staleTime: Infinity, // explanations are cached server-side
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (!status || status === "ready" || status === "failed") return false;
      return 3000; // outline still building - keep trying
    },
  });
}

export function useSourcesQuery(enabled = true) {
  const session = useSessionQuery(enabled);
  const identity =
    session.data?.username ??
    (session.data && session.data.authenticated === false ? "guest" : "pending");
  return useQuery({
    queryKey: [...queryKeys.sources, identity],
    queryFn: () => apiGet<SourceDocument[]>("/api/sources"),
    enabled: enabled && identity !== "pending",
    refetchInterval: (query) =>
      query.state.data?.some(
        (d) =>
          d.status === "indexing" ||
          d.status === "prepping" ||
          (d.meta?.prep_mode === "background" && !d.meta?.prep_complete),
      )
        ? 4000
        : false,
  });
}

export function useSessionQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.session,
    queryFn: async () => {
      const session = await apiGet<AuthSession>("/api/auth/session");
      // Only adopt a real token - never overwrite the guest CSRF with null when the
      // visitor is anonymous (the endpoint now returns 200 with a null token).
      if (session.csrf_token) setCsrfToken(session.csrf_token);
      return session;
    },
    enabled,
    retry: false,
  });
}

export function useArtifactQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.artifact(artifactId),
    queryFn: () => apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`),
    enabled: enabled && Boolean(artifactId),
    // NOTE: the indexing → ready poll is driven explicitly by the workspace page,
    // not by refetchInterval here. A fresh upload first settles this query on
    // status "pending" (awaiting page selection); React Query does not reliably
    // (re)arm a refetchInterval that was previously false when the status later
    // transitions to "indexing", so the indexing loader would freeze at its last
    // sampled percent until a manual refresh. See the explicit poll effect in
    // app/workspace/[artifactId]/page.tsx.
  });
}

export function useArtifactPagesQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.artifactPages(artifactId),
    queryFn: () => apiGet<PagesInfo>(`/api/artifacts/${artifactId}/pages`),
    enabled: enabled && Boolean(artifactId),
  });
}

export function useAssertionQuery(assertionId: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.assertion(assertionId ?? ""),
    queryFn: () =>
      apiGet<{ payload: Record<string, unknown>; title?: string }>(`/api/assertions/${assertionId}`),
    enabled: Boolean(assertionId),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    // Transient 502s during API rollouts left Learn stuck on "Could not load
    // question." until a hard refresh. Retry + focus refetch recover quietly.
    retry: 4,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
    refetchOnWindowFocus: true,
  });
}

/** Read / Learn / Test / Brainstorm each get their own conversation; other modes
 *  share "general". Mirrors `_CHAT_SURFACES` in backend/app/api/chat.py — a mode
 *  added to one and not the other silently merges into "general". */
export function chatSurfaceForMode(mode: string): string {
  return mode === "read" || mode === "learn" || mode === "test" || mode === "brainstorm" || mode === "socratic"
    ? mode
    : "general";
}

export function useChatMessagesQuery(artifactId: string, mode: string, enabled = true) {
  const surface = chatSurfaceForMode(mode);
  return useQuery({
    queryKey: queryKeys.chatMessages(artifactId, surface),
    queryFn: () =>
      apiGet<{ role: string; content: string; citations?: unknown }[]>(
        `/api/chat/threads/${artifactId}/messages?surface=${surface}`,
      ),
    enabled: enabled && Boolean(artifactId),
    retry: false,
    staleTime: 60_000,
    // Drop a surface's cache when you leave it, so returning re-fetches fresh
    // (picks up messages sent in another surface meanwhile).
    gcTime: 60_000,
  });
}

export function useInvalidateSources() {
  const qc = useQueryClient();
  return () => void qc.invalidateQueries({ queryKey: queryKeys.sources });
}
