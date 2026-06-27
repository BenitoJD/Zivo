"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, setCsrfToken } from "@/lib/api/client";
import type { ArtifactMeta, PagesInfo, SourceDocument } from "@/lib/types";

export type AuthSession = {
  username: string;
  csrf_token: string;
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
      setCsrfToken(session.csrf_token);
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
