"use client";

import { useState } from "react";
import { apiPost, apiPostForm, ensureGuestSession } from "@/lib/api/client";

type AddSourceSheetProps = {
  open: boolean;
  onClose: () => void;
  onUploaded: (artifactId: string) => void;
};

export function AddSourceSheet({ open, onClose, onUploaded }: AddSourceSheetProps) {
  const [tab, setTab] = useState<"file" | "url" | "paste" | "github">("file");
  const [url, setUrl] = useState("");
  const [paste, setPaste] = useState("");
  const [github, setGithub] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  async function uploadFile(file: File) {
    setBusy(true);
    setError(null);
    try {
      await ensureGuestSession();
      const form = new FormData();
      form.append("file", file);
      const data = await apiPostForm<{ id: string }>("/api/sources", form);
      onUploaded(data.id);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function importUrl(kind: "url" | "paste" | "github") {
    setBusy(true);
    setError(null);
    try {
      await ensureGuestSession();
      const path =
        kind === "github"
          ? "/api/sources/import-github"
          : kind === "paste"
            ? "/api/sources/import-text"
            : "/api/sources/import-url";
      const body =
        kind === "url"
          ? { url }
          : kind === "paste"
            ? { text: paste, title: "Pasted source" }
            : { github_url: github };
      const data = await apiPost<{ id: string }>(path, body);
      onUploaded(data.id);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet add-source-sheet" onClick={(e) => e.stopPropagation()}>
        <header className="add-source-sheet__header">
          <h2>Add source</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <nav className="add-source-sheet__tabs">
          {(["file", "url", "paste", "github"] as const).map((t) => (
            <button
              key={t}
              type="button"
              className={tab === t ? "is-active" : ""}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </nav>
        <div className="add-source-sheet__body">
          {tab === "file" && (
            <label className="add-source-sheet__drop">
              <input
                type="file"
                accept=".pdf,.txt,.md"
                disabled={busy}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void uploadFile(f);
                }}
              />
              Drop PDF or text file
            </label>
          )}
          {tab === "url" && (
            <>
              <input
                type="url"
                placeholder="https://…"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
              <button type="button" disabled={busy || !url} onClick={() => importUrl("url")}>
                Import URL
              </button>
            </>
          )}
          {tab === "paste" && (
            <>
              <textarea
                rows={8}
                placeholder="Paste article or notes…"
                value={paste}
                onChange={(e) => setPaste(e.target.value)}
              />
              <button type="button" disabled={busy || !paste.trim()} onClick={() => importUrl("paste")}>
                Use pasted text
              </button>
            </>
          )}
          {tab === "github" && (
            <>
              <input
                type="url"
                placeholder="https://github.com/owner/repo"
                value={github}
                onChange={(e) => setGithub(e.target.value)}
              />
              <button
                type="button"
                disabled={busy || !github}
                onClick={() => importUrl("github")}
              >
                Import GitHub repo
              </button>
            </>
          )}
          {error && <p className="add-source-sheet__error">{error}</p>}
        </div>
      </div>
    </div>
  );
}
