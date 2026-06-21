"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AddSourceSheet } from "@/components/AddSourceSheet";
import { AuthModal } from "@/components/AuthModal";
import { WorkspaceLayout } from "@/components/workspace/WorkspaceLayout";
import { apiGet, ensureGuestSession, setCsrfToken } from "@/lib/api/client";

export default function WorkspaceIndexPage() {
  const router = useRouter();
  const [addOpen, setAddOpen] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [username, setUsername] = useState<string | null>(null);

  useEffect(() => {
    void ensureGuestSession();
    apiGet<{ username: string; csrf_token: string }>("/api/auth/session")
      .then((s) => {
        setUsername(s.username);
        setCsrfToken(s.csrf_token);
      })
      .catch(() => setUsername(null));
  }, []);

  return (
    <>
      <header className="workspace-index__header">
        <span>Question Better.</span>
        <div className="workspace-index__actions">
          <button type="button" onClick={() => setAddOpen(true)}>
            Add source
          </button>
          {username ? (
            <span className="muted">@{username}</span>
          ) : (
            <button type="button" onClick={() => setAuthOpen(true)}>
              Sign in
            </button>
          )}
        </div>
      </header>
      <WorkspaceLayout
        emptyState={
          <div className="workspace-empty">
            <h2>Start learning</h2>
            <p className="muted">
              Add a PDF, article, or notes. We&apos;ll turn them into questions you can practice and discuss.
            </p>
            <button type="button" className="workspace-empty__cta" onClick={() => setAddOpen(true)}>
              Add your first source
            </button>
          </div>
        }
      />
      <AddSourceSheet
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onUploaded={(id) => router.push(`/workspace/${id}`)}
      />
      <AuthModal open={authOpen} onClose={() => setAuthOpen(false)} onSuccess={setUsername} />
    </>
  );
}
