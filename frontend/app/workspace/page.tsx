"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  Box,
  Button,
  Center,
  Group,
  Paper,
  Stack,
  Text,
  ThemeIcon,
  Title,
  Textarea,
} from "@mantine/core";
import {
  IconArrowRight,
  IconBookUpload,
  IconBulb,
  IconMessageCircle,
  IconUpload,
  IconFileText,
  IconSparkles,
} from "@tabler/icons-react";
import { useWorkspaceShell } from "@/app/workspace/layout";
import { motion, AnimatePresence } from "framer-motion";
import { apiUploadFile, apiPost, ensureGuestSession, isArtifactId } from "@/lib/api/client";
import { useInvalidateSources } from "@/lib/api/queries";
import { notifications } from "@mantine/notifications";

const FORMATS = ["PDF", "Word", "URL", "Paste", "GitHub"];

const STEPS = [
  {
    icon: IconUpload,
    title: "1. Index",
    body: "Upload lecture notes, PDFs, or articles.",
    color: "lavender",
  },
  {
    icon: IconBulb,
    title: "2. Practice",
    body: "Answer questions generated from the text.",
    color: "sage",
  },
  {
    icon: IconMessageCircle,
    title: "3. Perfect",
    body: "Resolve misconceptions with your source tutor.",
    color: "lavender",
  },
];

export default function WorkspaceIndexPage() {
  const router = useRouter();
  const { openAddSource } = useWorkspaceShell();
  const invalidateSources = useInvalidateSources();

  // State
  const [activeTab, setActiveTab] = useState<"file" | "paste">("file");
  const [pasteContent, setPasteContent] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  // Mouse coords for interactive spotlight
  const [mousePos, setMousePos] = useState({ x: "50%", y: "50%" });

  // Refs
  const uploadAbortRef = useRef<AbortController | null>(null);

  const handleMouseMove = (e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = `${((e.clientX - rect.left) / rect.width) * 100}%`;
    const y = `${((e.clientY - rect.top) / rect.height) * 100}%`;
    setMousePos({ x, y });
  };

  // Drag and drop handlers
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) {
      await handleFileUpload(file);
    }
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      await handleFileUpload(file);
    }
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
          if (total > 0) {
            setUploadProgress(Math.round((loaded / total) * 100));
          }
        },
      });

      notifications.show({ title: "Uploaded source", message: file.name, color: "sage" });
      void invalidateSources();
      if (isArtifactId(data?.id)) {
        router.push(`/workspace/${data.id}`);
      }
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
      if (isArtifactId(data?.id)) {
        router.push(`/workspace/${data.id}`);
      }
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

  return (
    <Box
      flex={1}
      px="lg"
      py="xl"
      onMouseMove={handleMouseMove}
      style={{
        position: "relative",
        overflowY: "auto",
        background: "var(--mantine-color-body)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {/* Ambient interactive light glow */}
      <Box
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          zIndex: 0,
          background: `radial-gradient(circle 500px at ${mousePos.x} ${mousePos.y}, rgba(123, 93, 166, 0.04), transparent 70%)`,
          transition: "background 0.1s ease-out",
        }}
      />

      <Stack align="center" gap={36} w="100%" maw={720} style={{ position: "relative", zIndex: 1 }}>
        {/* Editorial Header */}
        <Stack align="center" gap={8} ta="center">
          <Box
            style={{
              display: "inline-flex",
              alignItems: "center",
              padding: "4px 12px",
              borderRadius: "var(--mantine-radius-xl)",
              background: "var(--mantine-color-lavender-0)",
              border: "1px solid var(--mantine-color-lavender-2)",
            }}
          >
            <Text size="xs" fw={700} lts={1} c="lavender.7">
              STUDY DECK
            </Text>
          </Box>
          <Title
            order={2}
            style={{
              fontFamily: "var(--font-sans), sans-serif",
              fontWeight: 500,
              letterSpacing: "-0.02em",
              fontSize: "clamp(1.8rem, 4.5vw, 2.6rem)",
            }}
          >
            Where does understanding{" "}
            <Box component="span" c="lavender.7" style={{ fontWeight: 700 }}>
              begin?
            </Box>
          </Title>
          <Text size="sm" c="gray.6" maw={480} lh={1.6}>
            Drag in a file, import a link, or paste notes directly. Zivo will index your content and build tailored study loops.
          </Text>
        </Stack>

        {/* Main Interaction Deck */}
        <Paper
          radius="xl"
          p={{ base: "lg", md: "xl" }}
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
          {/* Deck tab selector */}
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

          <AnimatePresence mode="wait">
            {activeTab === "file" ? (
              <motion.div
                key="file"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                transition={{ duration: 0.28, ease: [0.32, 0.72, 0, 1] }}
              >
                <Box
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                  style={{
                    border: `1.5px dashed ${
                      isDragOver
                        ? "var(--mantine-color-lavender-5)"
                        : "var(--mantine-color-default-border)"
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
                  onClick={() => document.getElementById("dashboard-file-input")?.click()}
                  className="interactive-dropzone"
                >
                  <input
                    id="dashboard-file-input"
                    type="file"
                    style={{ display: "none" }}
                    accept=".pdf,.doc,.docx,.txt,.md,image/*"
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
                          PDF, Word, text, or images (max 100MB)
                        </Text>
                      </Stack>
                    )}
                  </Stack>
                </Box>
              </motion.div>
            ) : (
              <motion.div
                key="paste"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                transition={{ duration: 0.28, ease: [0.32, 0.72, 0, 1] }}
              >
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
                    loading={isBusy}
                    disabled={!pasteContent.trim()}
                    rightSection={<IconSparkles size={16} />}
                    onClick={handlePasteSubmit}
                    style={{
                      background: "var(--mantine-color-lavender-7)",
                      borderRadius: "var(--mantine-radius-md)",
                    }}
                  >
                    Build from paste
                  </Button>
                </Stack>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Quick link actions */}
          <Group gap="xs" justify="center" mt="xl" wrap="wrap">
            {FORMATS.map((tag) => (
              <Box
                key={tag}
                onClick={openAddSource}
                style={{
                  padding: "6px 14px",
                  borderRadius: "var(--mantine-radius-md)",
                  border: "1px solid var(--mantine-color-default-border)",
                  background: "var(--mantine-color-default-hover)",
                  fontSize: "11px",
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.03em",
                  color: "var(--mantine-color-gray-6)",
                  cursor: "pointer",
                  transition: "all 150ms ease",
                }}
                className="format-pill"
              >
                {tag}
              </Box>
            ))}
          </Group>
        </Paper>

        {/* Workflow steps timeline */}
        <Group grow align="stretch" gap="md" w="100%" visibleFrom="sm">
          {STEPS.map((s, i) => (
            <Paper
              key={s.title}
              radius="lg"
              p="lg"
              withBorder
              bg="gray.0"
              style={{
                border: "1px solid var(--mantine-color-default-border)",
                cursor: "default",
                transition: "transform 280ms cubic-bezier(0.32, 0.72, 0, 1), box-shadow 280ms cubic-bezier(0.32, 0.72, 0, 1)",
              }}
              className="step-timeline-card"
            >
              <Stack gap={10} align="center" ta="center">
                <ThemeIcon
                  size={40}
                  radius="xl"
                  variant="light"
                  color={s.color}
                  style={{
                    background: `var(--mantine-color-${s.color}-0)`,
                    border: `1px solid var(--mantine-color-${s.color}-2)`,
                  }}
                >
                  <s.icon size={20} style={{ color: `var(--mantine-color-${s.color}-7)` }} />
                </ThemeIcon>
                <Text
                  fw={600}
                  size="sm"
                  style={{ fontFamily: "var(--font-sans), sans-serif" }}
                >
                  {s.title}
                </Text>
                <Text size="xs" c="gray.6" lh={1.5}>
                  {s.body}
                </Text>
              </Stack>
            </Paper>
          ))}
        </Group>
      </Stack>

      <style>{`
        .interactive-dropzone:hover {
          transform: translateY(-1px);
        }
        .format-pill:hover {
          border-color: var(--mantine-color-lavender-4) !important;
          color: var(--mantine-color-lavender-7) !important;
          background: var(--mantine-color-gray-0) !important;
        }
        .step-timeline-card:hover {
          transform: translateY(-3px);
          box-shadow: var(--mantine-shadow-paper-lg) !important;
        }
      `}</style>
    </Box>
  );
}
