"use client";

import { useRef, useState } from "react";
import {
  Box,
  Button,
  Group,
  Paper,
  Stack,
  Text,
  TextInput,
  Textarea,
  ThemeIcon,
} from "@mantine/core";
import { IconBookUpload, IconSparkles } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { apiUploadFile, apiPost, ensureGuestSession, isArtifactId } from "@/lib/api/client";
import { useInvalidateSources } from "@/lib/api/queries";

const FORMATS = ["PDF", "Word", "URL", "Paste", "GitHub"] as const;
type DeckTab = "file" | "paste" | "link" | "github";
const TAG_TO_TAB: Record<(typeof FORMATS)[number], DeckTab> = {
  PDF: "file",
  Word: "file",
  URL: "link",
  Paste: "paste",
  GitHub: "github",
};

/**
 * The interactive "add a source" deck - file drop/picker + paste/URL/GitHub tabs.
 * Shared by the /workspace landing page and the sidebar's Add-source modal so the
 * import logic lives in exactly one place. Calls `onImported` with the new
 * artifact id on success (the caller decides whether to navigate, close, etc.).
 */
export function SourceImportDeck({ onImported }: { onImported: (id: string) => void }) {
  const invalidateSources = useInvalidateSources();

  const [activeTab, setActiveTab] = useState<DeckTab>("file");
  const [pasteContent, setPasteContent] = useState("");
  const [importUrl, setImportUrl] = useState("");
  const [importGithub, setImportGithub] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const uploadAbortRef = useRef<AbortController | null>(null);
  // Per-instance input ref (not a shared DOM id) so the page's deck and the modal's
  // deck can coexist without their hidden file inputs colliding.
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => setIsDragOver(false);

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) await handleFileUpload(file);
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) await handleFileUpload(file);
  };

  const handleFileUpload = async (file: File) => {
    uploadAbortRef.current?.abort();
    const abort = new AbortController();
    uploadAbortRef.current = abort;
    setIsBusy(true);
    setUploadProgress(0);

    try {
      await ensureGuestSession();
      const data = await apiUploadFile<{ id: string }>(file, {
        signal: abort.signal,
        onProgress: (loaded, total) => {
          if (total > 0) setUploadProgress(Math.round((loaded / total) * 100));
        },
      });

      notifications.show({ title: "Uploaded source", message: file.name, color: "sage" });
      void invalidateSources();
      if (isArtifactId(data?.id)) onImported(data.id);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      notifications.show({
        title: "Upload failed",
        message: e instanceof Error ? e.message : "Could not upload file.",
        color: "terracotta",
      });
    } finally {
      setIsBusy(false);
      setUploadProgress(null);
    }
  };

  const handlePasteSubmit = async () => {
    if (!pasteContent.trim()) return;
    setIsBusy(true);
    try {
      await ensureGuestSession();
      const data = await apiPost<{ id: string }>("/api/sources/import-text", {
        text: pasteContent,
        title: "Pasted source",
      });
      notifications.show({ title: "Imported notes", message: "Successfully created study guide", color: "sage" });
      void invalidateSources();
      if (isArtifactId(data?.id)) onImported(data.id);
    } catch (e) {
      notifications.show({
        title: "Import failed",
        message: e instanceof Error ? e.message : "Could not import text.",
        color: "terracotta",
      });
    } finally {
      setIsBusy(false);
    }
  };

  const runImport = async (kind: "url" | "github") => {
    const value = kind === "url" ? importUrl : importGithub;
    if (!value.trim()) return;
    setIsBusy(true);
    try {
      await ensureGuestSession();
      const path = kind === "github" ? "/api/sources/import-github" : "/api/sources/import-url";
      const body = kind === "github" ? { github_url: importGithub } : { url: importUrl };
      const data = await apiPost<{ id: string }>(path, body);
      notifications.show({ title: "Imported", message: "Source added", color: "sage" });
      setImportUrl("");
      setImportGithub("");
      void invalidateSources();
      if (isArtifactId(data?.id)) onImported(data.id);
    } catch (e) {
      notifications.show({
        title: "Import failed",
        message:
          e instanceof Error && e.message === "Sign in to add more documents"
            ? "Guest limit reached - delete a source or sign in to add more."
            : e instanceof Error
              ? e.message
              : "Could not import.",
        color: "terracotta",
      });
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <Paper
      radius="xl"
      p={{ base: "md", sm: "lg", md: "xl" }}
      shadow="paper-lg"
      bg="gray.0"
      w="100%"
      style={{
        border: "1px solid var(--mantine-color-default-border)",
        boxShadow: "var(--mantine-shadow-paper-lg)",
        overflow: "hidden",
        position: "relative",
      }}
    >
      <Group justify="center" gap="xs" mb="lg">
        <Button
          variant={activeTab === "file" ? "light" : "subtle"}
          color="gray"
          size="xs"
          onClick={() => setActiveTab("file")}
          style={{ borderRadius: "var(--mantine-radius-xl)" }}
        >
          Upload file
        </Button>
        <Button
          variant={activeTab === "paste" ? "light" : "subtle"}
          color="gray"
          size="xs"
          onClick={() => setActiveTab("paste")}
          style={{ borderRadius: "var(--mantine-radius-xl)" }}
        >
          Quick paste
        </Button>
      </Group>

      <div key={activeTab} className="tab-swap">
        {activeTab === "file" ? (
          <Box
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            style={{
              border: `1.5px dashed ${
                isDragOver ? "var(--mantine-color-lavender-5)" : "var(--mantine-color-default-border)"
              }`,
              background: isDragOver
                ? "var(--mantine-color-lavender-0)"
                : "var(--mantine-color-default-hover)",
              borderRadius: "var(--mantine-radius-lg)",
              padding: "36px 20px",
              textAlign: "center",
              cursor: "pointer",
              transition: "all 200ms ease",
            }}
            onClick={() => fileInputRef.current?.click()}
            className="interactive-dropzone"
          >
            <input
              ref={fileInputRef}
              type="file"
              style={{ display: "none" }}
              accept=".pdf,.doc,.docx,.txt,.md"
              onChange={handleFileChange}
              disabled={isBusy}
            />
            <Stack align="center" gap="sm">
              <ThemeIcon
                size={48}
                radius="xl"
                variant="light"
                color="lavender"
                style={{
                  background: "var(--mantine-color-lavender-0)",
                  border: "1.5px solid var(--mantine-color-lavender-2)",
                }}
              >
                <IconBookUpload size={24} style={{ color: "var(--mantine-color-lavender-7)" }} />
              </ThemeIcon>
              {isBusy ? (
                <Text size="xs" c="gray.6">
                  {uploadProgress !== null ? `Uploading… ${uploadProgress}%` : "Indexing source…"}
                </Text>
              ) : (
                <Stack gap={2}>
                  <Text size="sm" fw={600}>
                    {isDragOver ? "Drop file to upload" : "Drop textbook pages or click to select"}
                  </Text>
                  <Text size="xs" c="gray.5">
                    PDF, Word, or text (max 100MB)
                  </Text>
                </Stack>
              )}
            </Stack>
          </Box>
        ) : activeTab === "paste" ? (
          <Stack gap="md">
            <Textarea
              placeholder="Paste article, slides, or study notes here..."
              minRows={5}
              maxRows={8}
              value={pasteContent}
              onChange={(e) => setPasteContent(e.currentTarget.value)}
              disabled={isBusy}
              styles={{
                input: {
                  background: "var(--mantine-color-default-hover)",
                  border: "1px solid var(--mantine-color-default-border)",
                  fontFamily: "var(--font-sans), sans-serif",
                  fontSize: "0.875rem",
                  padding: "12px",
                },
              }}
            />
            <Button
              fullWidth
              size="md"
              color="lavender"
              loading={isBusy}
              disabled={!pasteContent.trim()}
              rightSection={<IconSparkles size={16} />}
              onClick={handlePasteSubmit}
            >
              Build from paste
            </Button>
          </Stack>
        ) : activeTab === "link" ? (
          <Stack gap="md">
            <TextInput
              placeholder="Paste a YouTube link, article, or web page…"
              value={importUrl}
              onChange={(e) => setImportUrl(e.currentTarget.value)}
              disabled={isBusy}
              size="md"
              radius="md"
              styles={{ input: { background: "var(--mantine-color-default-hover)", border: "1px solid var(--mantine-color-default-border)" } }}
            />
            <Text size="xs" c="dimmed" ta="center">
              Paste a YouTube video link and we&rsquo;ll study its transcript.
            </Text>
            <Button
              fullWidth
              size="md"
              color="lavender"
              loading={isBusy}
              disabled={!importUrl.trim()}
              rightSection={<IconSparkles size={16} />}
              onClick={() => void runImport("url")}
            >
              Import from link
            </Button>
          </Stack>
        ) : (
          <Stack gap="md">
            <TextInput
              placeholder="https://github.com/owner/repo"
              value={importGithub}
              onChange={(e) => setImportGithub(e.currentTarget.value)}
              disabled={isBusy}
              size="md"
              radius="md"
              styles={{ input: { background: "var(--mantine-color-default-hover)", border: "1px solid var(--mantine-color-default-border)" } }}
            />
            <Button
              fullWidth
              size="md"
              color="lavender"
              loading={isBusy}
              disabled={!importGithub.trim()}
              rightSection={<IconSparkles size={16} />}
              onClick={() => void runImport("github")}
            >
              Import repository
            </Button>
          </Stack>
        )}
      </div>

      <Group gap="xs" justify="center" mt="xl" wrap="wrap">
        {FORMATS.map((tag) => {
          const tab = TAG_TO_TAB[tag];
          const active = activeTab === tab;
          return (
            <Box
              key={tag}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: "6px 14px",
                borderRadius: "var(--mantine-radius-md)",
                border: `1px solid ${active ? "var(--mantine-color-lavender-3)" : "var(--mantine-color-default-border)"}`,
                background: active ? "var(--mantine-color-lavender-0)" : "var(--mantine-color-default-hover)",
                fontSize: "11px",
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.03em",
                color: active ? "var(--mantine-color-lavender-7)" : "var(--mantine-color-gray-6)",
                cursor: "pointer",
                transition: "all 150ms ease",
              }}
              className="format-pill"
            >
              {tag}
            </Box>
          );
        })}
      </Group>

      <style>{`
        /* Reliable CSS tab cross-fade (framer AnimatePresence stalls under React 19
           strict mode here, leaving content stuck invisible). Re-keyed per tab. */
        @keyframes tab-swap-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
        .tab-swap { animation: tab-swap-in 260ms cubic-bezier(0.32, 0.72, 0, 1) both; }
        @media (prefers-reduced-motion: reduce) { .tab-swap { animation: none; } }
        .interactive-dropzone:hover { transform: translateY(-1px); }
        .format-pill:hover {
          border-color: var(--mantine-color-lavender-4) !important;
          color: var(--mantine-color-lavender-7) !important;
          background: var(--mantine-color-gray-0) !important;
        }
      `}</style>
    </Paper>
  );
}
