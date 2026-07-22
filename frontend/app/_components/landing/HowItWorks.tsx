"use client";

import { Box, Container, Group, Paper, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import {
  IconUpload,
  IconListCheck,
  IconMessages,
  type Icon,
} from "@tabler/icons-react";
import { Reveal, Stagger } from "./motion";

type Step = { icon: Icon; num: string; title: string; body: string };

const STEPS: Step[] = [
  {
    icon: IconUpload,
    num: "01",
    title: "Upload your source",
    body: "Drop in a PDF, paste notes, or import slides. Zivo reads it and prepares the whole thing for study in seconds.",
  },
  {
    icon: IconListCheck,
    num: "02",
    title: "Get questions that test",
    body: "Pick the pages you care about. Get exam-style multiple-choice questions, each one tied to a real concept - with explanations.",
  },
  {
    icon: IconMessages,
    num: "03",
    title: "Ask and learn",
    body: "Stuck? Ask the tutor in plain language. It answers from your source and shows you exactly where, so understanding compounds.",
  },
];

export function HowItWorks() {
  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 80 }}>
      <Stack gap={36}>
        <Reveal>
          <Title
            order={2}
            ta="center"
            style={{
              fontFamily: "var(--font-sans), sans-serif",
              fontWeight: 500,
              fontSize: "clamp(1.6rem, 3.2vw, 2.3rem)",
              letterSpacing: "-0.015em",
            }}
          >
            From source to understanding in three steps.
          </Title>
        </Reveal>

        <Stagger>
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="lg" w="100%">
            {STEPS.map((s) => (
              <Reveal key={s.num} as="div">
                <Paper radius="lg" p="xl" shadow="paper" bg="gray.0" h="100%">
                  <Stack gap={16} h="100%">
                    <Group gap={12} wrap="nowrap" align="center">
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
                        <s.icon size={22} stroke={1.6} />
                      </Box>
                      <Text
                        size="xs"
                        fw={700}
                        tt="uppercase"
                        lts={1}
                        c="gray.5"
                        ff="monospace"
                      >
                        {s.num}
                      </Text>
                    </Group>
                    <Title
                      order={4}
                      style={{
                        fontFamily: "var(--font-sans), sans-serif",
                        fontWeight: 500,
                      }}
                    >
                      {s.title}
                    </Title>
                    <Text size="sm" c="gray.6" lh={1.6}>
                      {s.body}
                    </Text>
                  </Stack>
                </Paper>
              </Reveal>
            ))}
          </SimpleGrid>
        </Stagger>
      </Stack>
    </Container>
  );
}
