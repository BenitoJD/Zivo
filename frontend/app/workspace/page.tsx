"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AddSourceSheet } from "@/components/AddSourceSheet";
import { AuthModal } from "@/components/AuthModal";
import { WorkspaceLayout } from "@/components/workspace/WorkspaceLayout";
import { apiGet } from "@/lib/api/client";

export default function WorkspaceIndexPage() {
  const router = useRouter();
  const [addOpen, setAddOpen] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [username, setUsername] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ username: string }>("/api/auth/session")
      .then((s) => setUsername(s.username))
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
      <WorkspaceLayout />
      <AddSourceSheet
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onUploaded={(id) => router.push(`/workspace/${id}`)}
      />
      <AuthModal open={authOpen} onClose={() => setAuthOpen(false)} onSuccess={setUsername} />
    </>
  );
}
