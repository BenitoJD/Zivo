"use client";

import { useState } from "react";
import { Box, Container, Group, Stack, Text } from "@mantine/core";
import {
  IconSchool,
  IconCertificate,
  IconBriefcase,
  IconFlask,
  type Icon,
} from "@tabler/icons-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Reveal, EASE } from "./motion";

type Persona = {
  id: string;
  tab: string;
  icon: Icon;
  source: string;
  question: string;
  options: string[];
  answer: number;
};

const PERSONAS: Persona[] = [
  {
    id: "students",
    tab: "Students",
    icon: IconSchool,
    source: "Biology 101 — Cellular Respiration (lecture PDF)",
    question: "Where in the cell does the citric acid cycle take place?",
    options: ["Cytoplasm", "Mitochondrial matrix", "Cell membrane", "Nucleus"],
    answer: 1,
  },
  {
    id: "exam",
    tab: "Exam prep",
    icon: IconCertificate,
    source: "MCAT — Amino Acids & Proteins (review sheet)",
    question: "Which amino acid is most likely buried in a protein's hydrophobic core?",
    options: ["Lysine", "Glutamate", "Leucine", "Serine"],
    answer: 2,
  },
  {
    id: "professionals",
    tab: "Professionals",
    icon: IconBriefcase,
    source: "Onboarding deck — Q3 Security & Compliance policy",
    question: "When must a data-access request be escalated to the security team?",
    options: [
      "Never — managers approve all access",
      "Only for external contractors",
      "Whenever it touches customer PII",
      "Only during audits",
    ],
    answer: 2,
  },
  {
    id: "researchers",
    tab: "Researchers",
    icon: IconFlask,
    source: "Paper — Attention Is All You Need (uploaded PDF)",
    question: "What problem does multi-head attention primarily address?",
    options: [
      "Vanishing gradients in RNNs",
      "Attending to different representation subspaces at once",
      "Reducing model parameters",
      "Tokenizing rare words",
    ],
    answer: 1,
  },
];

/**
 * Wispr-Flow-style "Made for the way you work" — an interactive tab switcher.
 * Pick a persona and the panel morphs to show a real, source-grounded sample
 * question for that audience. Mirrors Wispr's "select one to see it in action".
 */
export function Personas() {
  const [active, setActive] = useState(0);
  const reduce = useReducedMotion();
  const persona = PERSONAS[active];

  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: 56, md: 88 }}>
      <Stack align="center" gap={36}>
        <Stack align="center" gap={14} ta="center" maw={640}>
          <Reveal>
            <Text size="xs" fw={600} tt="uppercase" lts={2} c="lavender.7">
              One engine, every subject
            </Text>
          </Reveal>
          <Reveal>
            <Box
              component="h2"
              style={{
                fontFamily: "var(--font-sans), sans-serif",
                fontWeight: 700,
                fontSize: "clamp(2rem, 4.5vw, 3.2rem)",
                letterSpacing: "-0.03em",
                lineHeight: 1.08,
                margin: 0,
              }}
            >
              Made for the way{" "}
              <Box component="span" style={{ color: "var(--mantine-color-lavender-7)" }}>
                you
              </Box>{" "}
              study.
            </Box>
          </Reveal>
          <Reveal>
            <Text size="md" c="gray.6" lh={1.6}>
              Select one to see Zivo in action.
            </Text>
          </Reveal>
        </Stack>

        {/* Tab pills */}
        <Group gap={10} justify="center" wrap="wrap">
          {PERSONAS.map((p, i) => {
            const isActive = i === active;
            return (
              <Box
                key={p.id}
                component="button"
                onClick={() => setActive(i)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "9px 18px",
                  borderRadius: 999,
                  cursor: "pointer",
                  fontFamily: "var(--font-sans), sans-serif",
                  fontWeight: 600,
                  fontSize: 14,
                  background: isActive ? "#644791" : "var(--mantine-color-gray-0)",
                  color: isActive ? "#FFFFFF" : "var(--mantine-color-gray-7)",
                  border: isActive
                    ? "1px solid #4E3774"
                    : "1px solid var(--mantine-color-default-border)",
                  transition: reduce
                    ? "none"
                    : "background 200ms ease, color 200ms ease, border-color 200ms ease",
                }}
              >
                <p.icon size={16} stroke={1.7} />
                {p.tab}
              </Box>
            );
          })}
        </Group>

        {/* Animated sample-question panel */}
        <Box
          style={{
            width: "min(680px, 100%)",
            background: "var(--mantine-color-gray-0)",
            border: "1px solid var(--mantine-color-default-border)",
            borderRadius: "var(--mantine-radius-xl)",
            boxShadow: "var(--mantine-shadow-paper-lg)",
            overflow: "hidden",
          }}
        >
          <AnimatePresence mode="wait">
            <motion.div
              key={persona.id}
              initial={reduce ? false : { opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduce ? undefined : { opacity: 0, y: -10 }}
              transition={{ duration: 0.32, ease: EASE }}
            >
              <Box
                style={{
                  padding: "14px 22px",
                  borderBottom: "1px solid var(--mantine-color-default-border)",
                  background: "var(--mantine-color-gray-1)",
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                }}
              >
                <persona.icon size={16} stroke={1.7} color="var(--mantine-color-lavender-7)" />
                <Text size="xs" fw={500} c="gray.6" style={{ fontFamily: "var(--font-sans)" }}>
                  Source: {persona.source}
                </Text>
              </Box>

              <Stack gap={16} p={{ base: "lg", md: 28 }}>
                <Text
                  fw={600}
                  size="lg"
                  c="gray.8"
                  style={{ fontFamily: "var(--font-sans)", letterSpacing: "-0.01em" }}
                >
                  {persona.question}
                </Text>
                <Stack gap={10}>
                  {persona.options.map((opt, oi) => {
                    const correct = oi === persona.answer;
                    return (
                      <Group
                        key={opt}
                        gap={12}
                        wrap="nowrap"
                        style={{
                          padding: "12px 16px",
                          borderRadius: "var(--mantine-radius-md)",
                          border: correct
                            ? "1.5px solid var(--mantine-color-lavender-5)"
                            : "1px solid var(--mantine-color-default-border)",
                          background: correct
                            ? "var(--mantine-color-lavender-0)"
                            : "var(--mantine-color-body)",
                        }}
                      >
                        <Box
                          style={{
                            width: 22,
                            height: 22,
                            flexShrink: 0,
                            borderRadius: "50%",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontSize: 12,
                            fontWeight: 700,
                            fontFamily: "var(--font-sans)",
                            background: correct ? "#644791" : "var(--mantine-color-gray-2)",
                            color: correct ? "#FFFFFF" : "var(--mantine-color-gray-6)",
                          }}
                        >
                          {String.fromCharCode(65 + oi)}
                        </Box>
                        <Text
                          size="sm"
                          c={correct ? "lavender.8" : "gray.7"}
                          fw={correct ? 600 : 500}
                          style={{ fontFamily: "var(--font-sans)" }}
                        >
                          {opt}
                        </Text>
                      </Group>
                    );
                  })}
                </Stack>
              </Stack>
            </motion.div>
          </AnimatePresence>
        </Box>
      </Stack>
    </Container>
  );
}
