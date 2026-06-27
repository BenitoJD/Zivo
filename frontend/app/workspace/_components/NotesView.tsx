"use client";

import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  Center,
  CopyButton,
  Group,
  Loader,
  Paper,
  SegmentedControl,
  Stack,
  Text,
  ThemeIcon,
} from "@mantine/core";
import { useMantineColorScheme } from "@mantine/core";
import {
  IconAlertTriangle,
  IconCheck,
  IconCopy,
  IconFileText,
  IconFileTypePdf,
  IconPhoto,
} from "@tabler/icons-react";
import { IconNotebook } from "@tabler/icons-react";
import { AssistantMarkdown } from "@/lib/chatMarkdown";
import { useNotesQuery, type NoteKind } from "@/lib/api/queries";
import { GenerateGate, markGenStarted, readGenStarted } from "./GenerateGate";

/**
 * Notes mode — Scribely-style. Turns a source into one cohesive, beautifully structured
 * study document (Markdown), with a Notes / Cheat-sheet toggle and PDF/PNG/Copy export.
 * Additive to MCQ; reuses the chat Markdown renderer for consistent Calm Paper styling.
 */
export function NotesView({
  artifactId,
  compact = false,
}: {
  artifactId: string;
  compact?: boolean;
}) {
  const [kind, setKind] = useState<NoteKind>("notes");
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  const sheetRef = useRef<HTMLDivElement>(null);
  const [exporting, setExporting] = useState<null | "pdf" | "png">(null);

  // Each kind (notes / cheatsheet) is gated and remembered independently.
  const mode = kind === "cheatsheet" ? "cheatsheet" : "notes";
  const [startedKinds, setStartedKinds] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!startedKinds[mode] && readGenStarted(artifactId, mode)) {
      setStartedKinds((prev) => ({ ...prev, [mode]: true }));
    }
  }, [artifactId, mode, startedKinds]);
  const started = Boolean(startedKinds[mode]);

  const { data, isError, refetch } = useNotesQuery(artifactId, kind, started);
  const status = data?.status;
  const content = data?.content ?? "";
  const ready = status === "ready" && content.trim().length > 0;

  function start() {
    markGenStarted(artifactId, mode);
    setStartedKinds((prev) => ({ ...prev, [mode]: true }));
  }

  async function exportPng() {
    if (!sheetRef.current) return;
    setExporting("png");
    try {
      const { toPng } = await import("html-to-image");
      const url = await toPng(sheetRef.current, { backgroundColor: "#ffffff", pixelRatio: 2 });
      const a = document.createElement("a");
      a.href = url;
      a.download = `${kind === "cheatsheet" ? "cheat-sheet" : "study-notes"}.png`;
      a.click();
    } catch {
      /* best-effort */
    } finally {
      setExporting(null);
    }
  }

  function exportPdf() {
    const node = sheetRef.current;
    if (!node) return;
    setExporting("pdf");
    try {
      const win = window.open("", "_blank", "width=820,height=1000");
      if (!win) return;
      const styles = Array.from(
        document.querySelectorAll('style, link[rel="stylesheet"]'),
      )
        .map((el) => el.outerHTML)
        .join("\n");
      win.document.write(
        `<!doctype html><html><head><meta charset="utf-8"><title>${
          kind === "cheatsheet" ? "Cheat sheet" : "Study notes"
        }</title>${styles}<style>body{background:#fff;margin:0;padding:32px;}` +
          `.zivo-print-sheet{max-width:760px;margin:0 auto;}@page{margin:16mm;}</style></head>` +
          `<body><div class="zivo-print-sheet">${node.innerHTML}</div></body></html>`,
      );
      win.document.close();
      win.focus();
      // Give the cloned stylesheets a beat to apply before printing.
      setTimeout(() => {
        win.print();
      }, 500);
    } finally {
      setExporting(null);
    }
  }

  const toggle = (
    <SegmentedControl
      size="xs"
      radius="xl"
      value={kind}
      onChange={(v) => setKind(v as NoteKind)}
      data={[
        { label: "Notes", value: "notes" },
        { label: "Cheat sheet", value: "cheatsheet" },
      ]}
    />
  );

  if (!started && !ready) {
    return (
      <Stack gap="lg" pb="xl">
        <Group justify="space-between">{toggle}</Group>
        <GenerateGate
          icon={<IconNotebook size={28} />}
          title={kind === "cheatsheet" ? "Build a cheat sheet" : "Generate study notes"}
          description={
            kind === "cheatsheet"
              ? "Condense this source into a one-page cheat sheet of the essentials."
              : "Generate clean, structured study notes from this source."
          }
          onStart={start}
          compact={compact}
        />
      </Stack>
    );
  }

  if (isError || status === "failed") {
    return (
      <Stack gap="lg" pb="xl">
        <Group justify="space-between">{toggle}</Group>
        <WaitState
          icon={<IconAlertTriangle size={26} />}
          title="Couldn’t build these notes"
          body="Something went wrong reading this material. Try again in a moment."
          action={
            <Button variant="light" color="lavender" radius="xl" onClick={() => void refetch()}>
              Try again
            </Button>
          }
        />
      </Stack>
    );
  }

  if (!ready) {
    return (
      <Stack gap="lg" pb="xl">
        <Group justify="space-between">{toggle}</Group>
        <WaitState
          icon={<Loader color="lavender" size="sm" />}
          title={kind === "cheatsheet" ? "Building your cheat sheet" : "Writing your study notes"}
          body="Reading the whole source and laying it out clearly — this takes a few moments…"
        />
      </Stack>
    );
  }

  return (
    <Stack gap="md" pb="xl">
      <Group justify="space-between" wrap="wrap" gap="sm">
        {toggle}
        <Group gap={6} wrap="nowrap">
          <CopyButton value={content} timeout={2000}>
            {({ copied, copy }) => (
              <Button
                size="xs"
                variant="default"
                radius="xl"
                leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
                onClick={copy}
              >
                {copied ? "Copied" : "Copy"}
              </Button>
            )}
          </CopyButton>
          <Button
            size="xs"
            variant="default"
            radius="xl"
            leftSection={<IconFileTypePdf size={14} />}
            loading={exporting === "pdf"}
            onClick={exportPdf}
          >
            PDF
          </Button>
          <Button
            size="xs"
            variant="default"
            radius="xl"
            leftSection={<IconPhoto size={14} />}
            loading={exporting === "png"}
            onClick={() => void exportPng()}
          >
            PNG
          </Button>
        </Group>
      </Group>

      <Paper
        ref={sheetRef}
        radius="lg"
        p={compact ? "md" : "xl"}
        withBorder
        bg={isDark ? "dark.7" : "white"}
        style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))" }}
      >
        <AssistantMarkdown content={content} isDark={isDark} />
      </Paper>
    </Stack>
  );
}

function WaitState({
  icon,
  title,
  body,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <Center mih={260}>
      <Stack align="center" gap="sm" ta="center" maw={440}>
        <ThemeIcon variant="light" color="lavender" radius="xl" size={54}>
          {icon ?? <IconFileText size={26} />}
        </ThemeIcon>
        <Text ff="var(--font-serif)" fz={24} fw={500} c="var(--mantine-color-text)">
          {title}
        </Text>
        <Text c="dimmed">{body}</Text>
        {action}
      </Stack>
    </Center>
  );
}
