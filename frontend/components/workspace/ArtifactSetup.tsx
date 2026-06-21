"use client";

import { useEffect, useState } from "react";
import { ensureGuestSession } from "@/lib/api/client";
import { PageRangePicker } from "@/components/PageRangePicker";
import { apiGet, apiPost } from "@/lib/api/client";

type ArtifactMeta = {
  id: string;
  status: string;
  meta?: { selected_range?: { from: number; to: number }; page_count?: number };
};

type PagesInfo = {
  page_count: number;
  status: string;
};

export function ArtifactSetup({
  artifactId,
  children,
}: {
  artifactId: string;
  children: React.ReactNode;
}) {
  const [artifact, setArtifact] = useState<ArtifactMeta | null>(null);
  const [pages, setPages] = useState<PagesInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    void ensureGuestSession();
    let cancelled = false;
    Promise.all([
      apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`),
      apiGet<PagesInfo>(`/api/artifacts/${artifactId}/pages`),
    ])
      .then(([art, pg]) => {
        if (!cancelled) {
          setArtifact(art);
          setPages(pg);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load source");
      });
    return () => {
      cancelled = true;
    };
  }, [artifactId]);

  const selectedRange = artifact?.meta?.selected_range;
  const pageCount = pages?.page_count ?? artifact?.meta?.page_count ?? 1;

  async function confirmRange(range: { from: number; to: number }) {
    setConfirming(true);
    setError(null);
    try {
      await apiPost(`/api/artifacts/${artifactId}/page-range`, range);
      const art = await apiGet<ArtifactMeta>(`/api/artifacts/${artifactId}`);
      setArtifact(art);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start indexing");
    } finally {
      setConfirming(false);
    }
  }

  if (error && !artifact) {
    return (
      <div className="workspace-empty">
        <p className="workspace-empty__error">{error}</p>
      </div>
    );
  }

  if (!artifact || !pages) {
    return (
      <div className="workspace-empty">
        <p className="muted">Loading source…</p>
      </div>
    );
  }

  if (!selectedRange) {
    return (
      <div className="workspace-empty workspace-empty--setup">
        <h2>Choose pages to study</h2>
        <p className="muted">Questions and chat stay scoped to your selection.</p>
        <PageRangePicker
          pageCount={pageCount}
          onConfirm={confirmRange}
        />
        {confirming && <p className="muted">Starting indexing…</p>}
        {error && <p className="workspace-empty__error">{error}</p>}
      </div>
    );
  }

  if (artifact.status === "indexing") {
    return (
      <div className="workspace-empty">
        <h2>Indexing your pages…</h2>
        <p className="muted">
          Pages {selectedRange.from}–{selectedRange.to} · This usually takes a minute.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
