"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDelete, apiGet, apiPost, setCsrfToken } from "@/lib/api/client";
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
  chatMessages: (artifactId: string) => ["chat", artifactId, "messages"] as const,
  topics: (id: string) => ["topics", id] as const,
  topicExplain: (id: string, key: string) => ["topics", id, "explain", key] as const,
  notes: (id: string, kind: string) => ["notes", id, kind] as const,
  flashcards: (id: string) => ["flashcards", id] as const,
  memoryPalace: (id: string, setting: string) => ["memory-palace", id, setting] as const,
  savedNotes: (id: string) => ["saved-notes", id] as const,
  quiz: (id: string, config: string) => ["quiz", id, config] as const,
};

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
      if (status === "ready" || status === "failed") return false;
      return document.visibilityState === "visible" ? 3000 : false;
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

export function useNotesQuery(artifactId: string, kind: NoteKind = "notes", enabled = true) {
  return useQuery({
    queryKey: queryKeys.notes(artifactId, kind),
    queryFn: () => apiGet<NotesResponse>(`/api/artifacts/${artifactId}/notes?kind=${kind}`),
    enabled: enabled && Boolean(artifactId),
    refetchInterval: (query) =>
      stillBuilding(query.state.data?.status) && document.visibilityState === "visible" ? 3000 : false,
  });
}

export function useFlashcardsQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.flashcards(artifactId),
    queryFn: () => apiGet<FlashcardsResponse>(`/api/artifacts/${artifactId}/flashcards`),
    enabled: enabled && Boolean(artifactId),
    refetchInterval: (query) =>
      stillBuilding(query.state.data?.status) && document.visibilityState === "visible" ? 3000 : false,
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
    refetchInterval: (query) =>
      stillBuilding(query.state.data?.status) && document.visibilityState === "visible" ? 3000 : false,
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

export type QuizQuestion = {
  // mcq_negative / assertion_reason / scenario / cloze are single-best-answer MCQ
  // variants — same payload shape as "mcq" (options + answer_index).
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
    refetchInterval: (query) =>
      stillBuilding(query.state.data?.status) && document.visibilityState === "visible" ? 3000 : false,
  });
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
      return 3000; // outline still building — keep trying
    },
  });
}

export function useSourcesQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.sources,
    queryFn: () => apiGet<SourceDocument[]>("/api/sources"),
    enabled,
    refetchInterval: (query) => {
      const docs = query.state.data;
      if (!docs?.some((d) => d.status === "indexing")) return false;
      return document.visibilityState === "visible" ? 4000 : false;
    },
  });
}

export function useSessionQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.session,
    queryFn: async () => {
      const session = await apiGet<AuthSession>("/api/auth/session");
      // Only adopt a real token — never overwrite the guest CSRF with null when the
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
    refetchInterval: (query) => {
      if (query.state.data?.status !== "indexing") return false;
      return document.visibilityState === "visible" ? 5000 : false;
    },
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
  });
}

export function useChatMessagesQuery(artifactId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.chatMessages(artifactId),
    queryFn: () => apiGet<{ role: string; content: string }[]>(`/api/chat/threads/${artifactId}/messages`),
    enabled: enabled && Boolean(artifactId),
    retry: false,
  });
}

export function useInvalidateSources() {
  const qc = useQueryClient();
  return () => void qc.invalidateQueries({ queryKey: queryKeys.sources });
}

export function useInvalidateArtifact(artifactId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: queryKeys.artifact(artifactId) });
    void qc.invalidateQueries({ queryKey: queryKeys.artifactPages(artifactId) });
  };
}
