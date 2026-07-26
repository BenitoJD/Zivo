"use client";

/**
 * Learn-mode pre-question lesson.
 *
 * Before a page's MCQs are served in Learn mode, the learner reads a short,
 * AI-written lesson that teaches the concepts that page's questions will test.
 * One "Start the questions" CTA dismisses it for that page and reveals the
 * MCQs. Mirrors the Calm Paper reading recipe: serif heading, serif body at a
 * generous line-height, the shared MCQ reading measure, and a single lavender
 * pill CTA. No markdown library — a tiny inline formatter handles the two marks
 * the lesson prompt is allowed to emit (`**bold**` and `- ` bullets).
 */

import { Box, Button, Center, Stack, Text, Title } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";

import { MCQ_CONTENT_MAX } from "@/app/_components/mcq/McqCard";
import { WaitState } from "./WaitState";

/** Render one paragraph's inline `**bold**` spans as <Text><b/></Text> nodes. */
function renderInline(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <Text
          key={i}
          component="b"
          fw={600}
          inherit
          style={{ fontColor: "inherit" }}
        >
          {part.slice(2, -2)}
        </Text>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

/**
 * Lightweight prose renderer for the lesson body. Handles only the two marks
 * the `lesson_page_system` prompt permits: blank-line-separated paragraphs and
 * `- ` bullets. Anything else renders as plain text — no markdown dependency.
 */
function LessonProse({ body }: { body: string }) {
  const blocks = body
    .split(/\n{2,}/)
    .map((b) => b.trim())
    .filter(Boolean);

  return (
    <Stack gap="sm" align="stretch">
      {blocks.map((block, i) => {
        const lines = block.split(/\n/).map((l) => l.trim()).filter(Boolean);
        const isBulletList = lines.every((l) => l.startsWith("- "));
        if (isBulletList && lines.length > 0) {
          return (
            <Box
              key={i}
              component="ul"
              m={0}
              p={0}
              style={{ listStyle: "none", paddingLeft: 0 }}
            >
              {lines.map((line, j) => (
                <Text
                  key={j}
                  component="li"
                  c="var(--mantine-color-text)"
                  style={{
                    fontFamily: "var(--font-serif), Georgia, serif",
                    lineHeight: 1.7,
                    fontSize: "1.0625rem",
                    paddingLeft: "1.1rem",
                    position: "relative",
                    marginTop: j === 0 ? 0 : 4,
                  }}
                >
                  <span
                    style={{
                      position: "absolute",
                      left: 0,
                      top: "0.62em",
                      width: 5,
                      height: 5,
                      borderRadius: "50%",
                      background: "var(--mantine-color-lavender-6)",
                      display: "inline-block",
                    }}
                  />
                  {renderInline(line.slice(2))}
                </Text>
              ))}
            </Box>
          );
        }
        return (
          <Text
            key={i}
            c="var(--mantine-color-text)"
            style={{
              fontFamily: "var(--font-serif), Georgia, serif",
              lineHeight: 1.75,
              fontSize: "1.0625rem",
            }}
          >
            {renderInline(block)}
          </Text>
        );
      })}
    </Stack>
  );
}

export function LessonScreen({
  title,
  body,
  status,
  onStart,
}: {
  title: string | null;
  body: string | null;
  status: string;
  onStart: () => void;
}) {
  // The lesson is still being written. Show the calm preparing state — same pet
  // playground used by NotesView so the wait feels consistent across the app.
  if (status === "generating" || !body) {
    return (
      <WaitState
        pet
        title="Writing your lesson"
        body="Reading this page and laying out the key ideas clearly - this takes a few moments…"
      />
    );
  }

  return (
    <Center py="lg" px="md" h="100%" style={{ animation: "lesson-in 420ms cubic-bezier(0.32,0.72,0,1) both" }}>
      <style>{`
        @keyframes lesson-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
        @media (prefers-reduced-motion: reduce) { [style*="lesson-in"] { animation: none !important; } }
      `}</style>
      <Stack align="center" gap="lg" maw={MCQ_CONTENT_MAX} w="100%">
        <Stack align="center" gap="xs" w="100%">
          {title ? (
            <Title
              order={2}
              ta="center"
              fw={500}
              style={{
                letterSpacing: "-0.01em",
                lineHeight: 1.25,
                fontFamily: "var(--font-serif), Georgia, serif",
              }}
            >
              {title}
            </Title>
          ) : null}
        </Stack>

        <Box w="100%" style={{ textAlign: "left" }}>
          <LessonProse body={body} />
        </Box>

        <Button
          size="md"
          radius="xl"
          color="lavender"
          rightSection={<IconArrowRight size={18} stroke={2.2} />}
          onClick={onStart}
          mt="xs"
        >
          Start the questions
        </Button>
      </Stack>
    </Center>
  );
}
