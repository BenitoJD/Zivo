"use client";

import { useEffect, useState } from "react";
import { Box, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { EASE } from "./motion";

type Option = { letter: string; text: string; correct: boolean };

type Question = {
  stem: string;
  options: Option[];
  explanation: string;
};

const QUESTIONS: Question[] = [
  {
    stem: "Which molecule do plants use to capture light energy during photosynthesis?",
    options: [
      { letter: "A", text: "Chlorophyll", correct: true },
      { letter: "B", text: "Glucose", correct: false },
      { letter: "C", text: "Oxygen", correct: false },
      { letter: "D", text: "Carbon dioxide", correct: false },
    ],
    explanation: "Chlorophyll absorbs light most strongly in the blue and red bands.",
  },
  {
    stem: "In a perfectly competitive market, a firm's marginal revenue equals…",
    options: [
      { letter: "A", text: "Average total cost", correct: false },
      { letter: "B", text: "Market price", correct: true },
      { letter: "C", text: "Marginal cost", correct: false },
      { letter: "D", text: "Total revenue", correct: false },
    ],
    explanation: "Each extra unit sells at the market price, so price = marginal revenue.",
  },
  {
    stem: "Which data structure uses FIFO ordering?",
    options: [
      { letter: "A", text: "Stack", correct: false },
      { letter: "B", text: "Tree", correct: false },
      { letter: "C", text: "Queue", correct: true },
      { letter: "D", text: "Heap", correct: false },
    ],
    explanation: "A queue removes in the same order it inserted - first in, first out.",
  },
];

/**
 * The landing page's signature visual, made alive.
 *
 * Cycles through a short list of MCQs: the question enters, the correct option
 * is revealed with the calm `sage` treatment, the tutor explanation fades in,
 * then it advances. When prefers-reduced-motion is set, a single static card
 * is shown instead of the auto-advancing loop.
 */
export function ProductMock() {
  const reduce = useReducedMotion();
  const [index, setIndex] = useState(0);
  const [phase, setPhase] = useState<"ask" | "reveal">("ask");

  useEffect(() => {
    if (reduce) return; // static card, no timers
    const t1 = setTimeout(() => setPhase("reveal"), 1700);
    const t2 = setTimeout(() => {
      setPhase("ask");
      setIndex((i) => (i + 1) % QUESTIONS.length);
    }, 4600);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, [index, reduce]);

  const q = QUESTIONS[index];

  if (reduce) {
    return <StaticCard q={QUESTIONS[0]} revealed />;
  }

  return (
    <Paper
      shadow="paper-lg"
      radius="xl"
      p={{ base: "lg", md: 28 }}
      bg="gray.0"
      style={{
        position: "relative",
        overflow: "hidden",
        border: "1px solid var(--mantine-color-default-border)",
        boxShadow: "var(--mantine-shadow-paper-lg)",
      }}
    >
      <Box
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          zIndex: 0,
          background: "radial-gradient(circle 300px at 80% 20%, rgba(123, 93, 166, 0.04), transparent 70%)",
        }}
      />
      <Stack gap={18} style={{ position: "relative", zIndex: 1 }}>
        <AnimatePresence mode="wait">
          <motion.div
            key={index}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.4, ease: EASE }}
          >
            <Stack gap={14}>
              <Title
                order={3}
                style={{
                  fontFamily: "var(--font-serif), Georgia, serif",
                  fontWeight: 500,
                  lineHeight: 1.3,
                  minHeight: "2.6em",
                }}
              >
                {q.stem}
              </Title>
            </Stack>
          </motion.div>
        </AnimatePresence>

        <Stack gap={10}>
          {q.options.map((opt) => {
            const showCorrect = phase === "reveal" && opt.correct;
            return (
              <Paper
                key={opt.letter}
                radius="md"
                p="sm"
                withBorder
                style={{
                  borderColor: showCorrect
                    ? "var(--mantine-color-sage-6)"
                    : undefined,
                  background: showCorrect
                    ? "var(--mantine-color-sage-0)"
                    : undefined,
                  transition:
                    "background 420ms cubic-bezier(0.32, 0.72, 0, 1), border-color 420ms cubic-bezier(0.32, 0.72, 0, 1)",
                }}
              >
                <Group gap={12} wrap="nowrap">
                  <Text
                    size="sm"
                    fw={700}
                    c={showCorrect ? "sage.8" : "gray.6"}
                    ff="monospace"
                  >
                    {opt.letter}
                  </Text>
                  <Text
                    size="sm"
                    c={showCorrect ? "sage.9" : "gray.8"}
                    fw={showCorrect ? 600 : 400}
                  >
                    {opt.text}
                  </Text>
                </Group>
              </Paper>
            );
          })}
        </Stack>

        {/* The explanation's space is reserved at all times so revealing it fades the
            text in WITHOUT growing the card - no page jump. */}
        <Box style={{ minHeight: "2.8em", display: "flex", alignItems: "flex-start", justifyContent: "center" }}>
          <Text
            size="xs"
            c="gray.5"
            fs="italic"
            ta="center"
            style={{
              fontFamily: "var(--font-serif)",
              opacity: phase === "reveal" ? 1 : 0,
              transform: phase === "reveal" ? "none" : "translateY(6px)",
              transition: "opacity 420ms cubic-bezier(0.32,0.72,0,1), transform 420ms cubic-bezier(0.32,0.72,0,1)",
            }}
          >
            {q.explanation}
          </Text>
        </Box>
      </Stack>
    </Paper>
  );
}

function StaticCard({ q, revealed }: { q: Question; revealed: boolean }) {
  return (
    <Paper shadow="paper-lg" radius="xl" p={{ base: "lg", md: 28 }} bg="gray.0">
      <Stack gap={18}>
        <Box>
          <Stack gap={14}>
            <Title
              order={3}
              style={{
                fontFamily: "var(--font-serif), Georgia, serif",
                fontWeight: 500,
                lineHeight: 1.3,
              }}
            >
              {q.stem}
            </Title>
          </Stack>
        </Box>
        <Stack gap={10}>
          {q.options.map((opt) => {
            const showCorrect = revealed && opt.correct;
            return (
              <Paper
                key={opt.letter}
                radius="md"
                p="sm"
                withBorder
                style={{
                  borderColor: showCorrect
                    ? "var(--mantine-color-sage-6)"
                    : undefined,
                  background: showCorrect
                    ? "var(--mantine-color-sage-0)"
                    : undefined,
                }}
              >
                <Group gap={12} wrap="nowrap">
                  <Text
                    size="sm"
                    fw={700}
                    c={showCorrect ? "sage.8" : "gray.6"}
                    ff="monospace"
                  >
                    {opt.letter}
                  </Text>
                  <Text
                    size="sm"
                    c={showCorrect ? "sage.9" : "gray.8"}
                    fw={showCorrect ? 600 : 400}
                  >
                    {opt.text}
                  </Text>
                </Group>
              </Paper>
            );
          })}
        </Stack>
        <Text
          size="xs"
          c="gray.5"
          fs="italic"
          ta="center"
          style={{ fontFamily: "var(--font-serif)" }}
        >
          {q.explanation}
        </Text>
      </Stack>
    </Paper>
  );
}
