"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Box,
  Button,
  Group,
  Modal,
  Progress,
  Stack,
  Tabs,
  Text,
  Textarea,
  TextInput,
} from "@mantine/core";
import { Dropzone, MIME_TYPES } from "@mantine/dropzone";
import { IconFileText, IconLink, IconUpload, IconX } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { apiPost, apiUploadFile, ensureGuestSession, isArtifactId } from "@/lib/api/client";
import { useInvalidateSources } from "@/lib/api/queries";

/** Frosted backdrop for modals. */
const MODAL_OVERLAY_PROPS = { backgroundOpacity: 0.45, blur: 8 } as const;

type AddSourceTab = "file" | "url" | "paste" | "github";

/**
 * Add-to-library modal. Handles file upload (chunked, abortable) and URL /
 * paste / GitHub imports. On success it routes to the new artifact and
 * refreshes the source list.
 */
export function AddSourceModal({
  opened,
  onClose,
  isMobile,
}: {
  opened: boolean;
  onClose: () => void;
  isMobile: boolean;
}) {
  const router = useRouter();
  const invalidateSources = useInvalidateSources();

  const [importUrl, setImportUrl] = useState("");
  const [importPaste, setImportPaste] = useState("");
  const [importGithub, setImportGithub] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [addSourceTab, setAddSourceTab] = useState<AddSourceTab>("file");
  const uploadInFlight = useRef(false);
  const uploadAbortRef = useRef<AbortController | null>(null);

  function onUploaded(id: string | undefined) {
    if (!isArtifactId(id)) {
      notifications.show({
        title: "Could not open source",
        message: "Upload finished without a valid document id. Try again from the library.",
        color: "terracotta",
      });
      return;
    }
    void invalidateSources();
    router.push(`/workspace/${id}`);
  }

  async function uploadFile(file: File) {
    if (uploadInFlight.current) return;
    uploadInFlight.current = true;
    uploadAbortRef.current?.abort();
    const abort = new AbortController();
    uploadAbortRef.current = abort;
    setImportBusy(true);
    setUploadProgress(0);
    try {
      await ensureGuestSession();
      const data = await apiUploadFile<{ id: string }>(file, {
        signal: abort.signal,
        onProgress: (loaded, total) => {
          if (total > 0) setUploadProgress(Math.round((loaded / total) * 100));
        },
      });
      notifications.show({ title: "Uploaded", message: file.name, color: "sage" });
      setImportUrl("");
      setImportPaste("");
      setImportGithub("");
      onUploaded(data.id);
      onClose();
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      const message =
        e instanceof Error && e.name === "TimeoutError"
          ? "Upload timed out. Try a smaller file or check your connection."
          : e instanceof Error
            ? e.message === "Sign in to add more documents"
              ? "Guest limit reached — delete a source or sign in to add more."
              : e.message
            : "Unknown error";
      notifications.show({ title: "Upload failed", message, color: "terracotta" });
    } finally {
      if (uploadAbortRef.current === abort) uploadAbortRef.current = null;
      uploadInFlight.current = false;
      setImportBusy(false);
      setUploadProgress(null);
    }
  }

  async function runImport(kind: "url" | "paste" | "github") {
    setImportBusy(true);
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
          ? { url: importUrl }
          : kind === "paste"
            ? { text: importPaste, title: "Pasted source" }
            : { github_url: importGithub };
      const data = await apiPost<{ id: string }>(path, body);
      notifications.show({ title: "Imported", message: "Source added", color: "sage" });
      setImportUrl("");
      setImportPaste("");
      setImportGithub("");
      onUploaded(data.id);
      onClose();
    } catch (e) {
      notifications.show({
        title: "Import failed",
        message:
          e instanceof Error && e.message === "Sign in to add more documents"
            ? "Guest limit reached — delete a source or sign in to add more."
            : e instanceof Error
              ? e.message
              : "Unknown error",
        color: "terracotta",
      });
    } finally {
      setImportBusy(false);
    }
  }

  function handleClose() {
    if (importBusy) {
      uploadAbortRef.current?.abort();
      uploadInFlight.current = false;
      setImportBusy(false);
      setUploadProgress(null);
    }
    onClose();
  }

  return (
    <Modal
      opened={opened}
      onClose={handleClose}
      title="Add to library"
      size="lg"
      fullScreen={isMobile}
      centered
      overlayProps={MODAL_OVERLAY_PROPS}
      closeOnClickOutside={!importBusy}
      closeOnEscape={!importBusy}
      padding={isMobile ? "md" : undefined}
    >
      <Text size="sm" c="gray.6" mb="lg" lh={1.5}>
        Upload a file, import a link, or paste notes to generate practice questions.
      </Text>
      <Tabs
        value={addSourceTab}
        onChange={(value) => value && setAddSourceTab(value as AddSourceTab)}
        keepMounted={false}
      >
        <Tabs.List grow mb="md">
          <Tabs.Tab value="file" leftSection={<IconUpload size={16} />} aria-label="Upload a file">
            <Box visibleFrom="sm" component="span">
              File
            </Box>
          </Tabs.Tab>
          <Tabs.Tab value="url" leftSection={<IconLink size={16} />} aria-label="Import from a link">
            <Box visibleFrom="sm" component="span">
              Link
            </Box>
          </Tabs.Tab>
          <Tabs.Tab value="paste" leftSection={<IconFileText size={16} />} aria-label="Paste text">
            <Box visibleFrom="sm" component="span">
              Paste
            </Box>
          </Tabs.Tab>
          <Tabs.Tab value="github" leftSection={<IconLink size={16} />} aria-label="Import a GitHub repository">
            <Box visibleFrom="sm" component="span">
              GitHub
            </Box>
          </Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="file" pt="md">
          <Dropzone
            onDrop={(files) => files[0] && void uploadFile(files[0])}
            accept={[MIME_TYPES.pdf, MIME_TYPES.doc, MIME_TYPES.docx, "text/plain", "text/markdown", "image/*"]}
            loading={importBusy}
            maxFiles={1}
            radius="md"
            className="zivo-dropzone"
          >
            {importBusy ? (
              <Stack align="center" justify="center" gap="sm" mih={{ base: 160, sm: 220 }} px="md">
                <Progress value={uploadProgress ?? 0} size="sm" w="100%" maw={280} animated={uploadProgress === null} />
                <Text size="sm" c="gray.6">
                  {uploadProgress !== null ? `Uploading… ${uploadProgress}%` : "Uploading…"}
                </Text>
              </Stack>
            ) : (
              <Group justify="center" gap="xl" mih={{ base: 160, sm: 220 }} style={{ pointerEvents: "none" }}>
                <Dropzone.Accept>
                  <IconUpload size={52} stroke={1.5} />
                </Dropzone.Accept>
                <Dropzone.Reject>
                  <IconX size={52} stroke={1.5} />
                </Dropzone.Reject>
                <Dropzone.Idle>
                  <Stack align="center" gap="xs">
                    <IconUpload size={48} stroke={1.5} />
                    <Text size="sm" fw={500}>
                      Drop a file here or click to browse
                    </Text>
                    <Text size="xs" c="gray.6">
                      PDF, Word, text, or image
                    </Text>
                  </Stack>
                </Dropzone.Idle>
              </Group>
            )}
          </Dropzone>
        </Tabs.Panel>

        <Tabs.Panel value="url" pt="md">
          <Stack>
            <TextInput
              label="Public URL"
              placeholder="https://…"
              value={importUrl}
              onChange={(e) => setImportUrl(e.currentTarget.value)}
              disabled={importBusy}
            />
            <Button
              fullWidth
              loading={importBusy}
              disabled={!importUrl.trim()}
              onClick={() => void runImport("url")}
            >
              Import URL
            </Button>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="paste" pt="md">
          <Stack>
            <Textarea
              label="Pasted text"
              placeholder="Paste article or notes…"
              value={importPaste}
              onChange={(e) => setImportPaste(e.currentTarget.value)}
              disabled={importBusy}
              minRows={6}
            />
            <Button
              fullWidth
              loading={importBusy}
              disabled={!importPaste.trim()}
              onClick={() => void runImport("paste")}
            >
              Use pasted text
            </Button>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="github" pt="md">
          <Stack>
            <TextInput
              label="GitHub repository"
              placeholder="https://github.com/owner/repo"
              value={importGithub}
              onChange={(e) => setImportGithub(e.currentTarget.value)}
              disabled={importBusy}
            />
            <Button
              fullWidth
              loading={importBusy}
              disabled={!importGithub.trim()}
              onClick={() => void runImport("github")}
            >
              Import repository
            </Button>
          </Stack>
        </Tabs.Panel>
      </Tabs>
      <style>{`
        .zivo-dropzone {
          border: 1.5px dashed var(--mantine-color-default-border) !important;
          background: var(--mantine-color-gray-0) !important;
          transition: all 280ms cubic-bezier(0.32, 0.72, 0, 1) !important;
          cursor: pointer;
        }
        .zivo-dropzone:hover {
          border-color: var(--mantine-color-lavender-5) !important;
          background: var(--mantine-color-lavender-0) !important;
          transform: scale(0.995);
        }
      `}</style>
    </Modal>
  );
}
