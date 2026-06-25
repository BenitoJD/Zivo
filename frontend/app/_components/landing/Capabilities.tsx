"use client";

import { Box, Container, Group, Paper, Stack, Text, Title } from "@mantine/core";
import {
  IconUpload,
  IconBulb,
  IconMessageCircle,
  type Icon,
} from "@tabler/icons-react";
import { Reveal, Stagger } from "./motion";

type Feature = { icon: Icon; title: string; body: string };

const FEATURES: Feature[] = [
  {
    icon: IconUpload,
    title: "Upload anything",
    body: "A textbook chapter, lecture slides, an article, your own notes — even a GitHub repo. Zivo reads it and gets to work.",
  },
  {
    icon: IconBulb,
    title: "Questions that test",
    body: "Exam-style multiple choice, scoped exactly to the pages you chose. No vague prompts — each one targets a real concept.",
  },
  {
    icon: IconMessageCircle,
    title: "A tutor that knows your source",
    body: "Ask follow-ups in plain language. The tutor answers from the same material you're studying, so it never makes things up.",
  },
];

export function Capabilities() {
  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 72 }}>
      <Stack gap={36}>
        <Reveal>
          <Title
            order={2}
            ta="center"
            style={{
              fontFamily: "var(--font-serif), Georgia, serif",
              fontWeight: 500,
              fontSize: "clamp(1.6rem, 3.2vw, 2.3rem)",
              letterSpacing: "-0.015em",
            }}
          >
            Study the way understanding is built.
          </Title>
        </Reveal>

        <Stagger
          style={{ display: "flex", flexWrap: "wrap", gap: "var(--mantine-spacing-lg)" }}
        >
          <Group gap="lg" grow align="stretch" w="100%">
            {FEATURES.map((f) => (
              <Reveal key={f.title} as="div">
                <Paper
                  radius="lg"
                  p="xl"
                  shadow="paper"
                  bg="gray.0"
                  h="100%"
                  className="capability-card"
                  style={{
                    transition: "transform 280ms cubic-bezier(0.32, 0.72, 0, 1), box-shadow 280ms cubic-bezier(0.32, 0.72, 0, 1), border-color 280ms cubic-bezier(0.32, 0.72, 0, 1)",
                    border: "1px solid var(--mantine-color-default-border)",
                    cursor: "default",
                  }}
                >
                  <Stack gap={14} h="100%">
                    <Box
                      style={{
                        width: 44,
                        height: 44,
                        borderRadius: "var(--mantine-radius-md)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        background: "var(--mantine-color-lavender-0)",
                        color: "var(--mantine-color-lavender-7)",
                      }}
                    >
                      <f.icon size={22} stroke={1.6} />
                    </Box>
                    <Title
                      order={4}
                      style={{
                        fontFamily: "var(--font-serif), Georgia, serif",
                        fontWeight: 500,
                      }}
                    >
                      {f.title}
                    </Title>
                    <Text size="sm" c="gray.6" lh={1.6}>
                      {f.body}
                    </Text>
                  </Stack>
                </Paper>
              </Reveal>
            ))}
          </Group>
        </Stagger>
      </Stack>
      <style>{`
        .capability-card:hover {
          transform: translateY(-4px);
          box-shadow: var(--mantine-shadow-paper-lg) !important;
          border-color: var(--mantine-color-lavender-3) !important;
        }
      `}</style>
    </Container>
  );
}
