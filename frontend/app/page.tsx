"use client";

import Link from "next/link";
import { Box, Button, Center, Container, Group, Paper, Stack, Text, Title } from "@mantine/core";
import {
  IconArrowRight,
  IconBook2,
  IconBulb,
  IconMessageCircle,
  IconUpload,
} from "@tabler/icons-react";
import { BrandMark } from "@/app/_components/BrandMark";

const FEATURES = [
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

export default function LandingPage() {
  return (
    <Box bg="var(--mantine-color-body)" style={{ minHeight: "100dvh" }}>
      {/* Top nav */}
      <Container size="lg" px={{ base: "md", md: "lg" }}>
        <Group justify="space-between" h={72} wrap="nowrap">
          <BrandMark height={30} />
          <Group gap="sm" wrap="nowrap">
            <Button component={Link} href="/login" variant="subtle" color="gray">
              Sign in
            </Button>
            <Button component={Link} href="/workspace" variant="default">
              Start studying
            </Button>
          </Group>
        </Group>
      </Container>

      {/* Hero */}
      <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 60 }}>
        <Group align="center" grow wrap="nowrap" gap={48}>
          <Stack gap={28} style={{ maxWidth: 560 }}>
            <Stack gap={16}>
              <Text
                size="sm"
                fw={600}
                tt="uppercase"
                lts={2}
                c="lavender.7"
                style={{ fontFamily: "var(--font-sans)" }}
              >
                Question Better.
              </Text>
              <Title
                order={1}
                style={{
                  fontFamily: "var(--font-serif), Georgia, serif",
                  fontWeight: 500,
                  lineHeight: 1.08,
                  fontSize: "clamp(2.2rem, 5vw, 3.5rem)",
                  letterSpacing: "-0.02em",
                }}
              >
                Turn what you read into questions that actually test it.
              </Title>
            </Stack>
            <Text size="lg" c="gray.6" maw={520} lh={1.6}>
              Upload anything. Get exam-style questions, scoped exactly to what you read, with a
              tutor that knows your source. Measure and improve understanding — one question at a
              time.
            </Text>
            <Group gap="sm" wrap="nowrap">
              <Button
                size="lg"
                component={Link}
                href="/workspace"
                rightSection={<IconArrowRight size={18} stroke={1.75} />}
              >
                Start studying
              </Button>
              <Button size="lg" variant="subtle" color="gray" component={Link} href="/signup">
                Create an account
              </Button>
            </Group>
          </Stack>

          {/* Hero visual — a calm mock of the study question card */}
          <Box visibleFrom="md">
            <Paper shadow="paper-lg" radius="xl" p={{ base: "lg", md: 28 }} bg="gray.0">
              <Stack gap={18}>
                <Group gap={8}>
                  <Text size="xs" fw={600} c="gray.6" tt="uppercase" lts={1}>
                    Question 3 of 10
                  </Text>
                  <Text size="xs" c="gray.5">·</Text>
                  <Text size="xs" c="gray.6">Page 7</Text>
                </Group>
                <Title
                  order={3}
                  style={{
                    fontFamily: "var(--font-serif), Georgia, serif",
                    fontWeight: 500,
                    lineHeight: 1.3,
                  }}
                >
                  Which molecule do plants use to capture light energy during photosynthesis?
                </Title>
                <Stack gap={10}>
                  {[
                    { letter: "A", text: "Chlorophyll", ticked: true },
                    { letter: "B", text: "Glucose", ticked: false },
                    { letter: "C", text: "Oxygen", ticked: false },
                    { letter: "D", text: "Carbon dioxide", ticked: false },
                  ].map((opt) => (
                    <Paper
                      key={opt.letter}
                      radius="md"
                      p="sm"
                      withBorder
                      style={{
                        borderColor: opt.ticked ? "var(--mantine-color-sage-6)" : undefined,
                        background: opt.ticked ? "var(--mantine-color-sage-0)" : undefined,
                      }}
                    >
                      <Group gap={12} wrap="nowrap">
                        <Text
                          size="sm"
                          fw={700}
                          c={opt.ticked ? "sage.8" : "gray.6"}
                          ff="monospace"
                        >
                          {opt.letter}
                        </Text>
                        <Text size="sm" c={opt.ticked ? "sage.9" : "gray.8"} fw={opt.ticked ? 600 : 400}>
                          {opt.text}
                        </Text>
                      </Group>
                    </Paper>
                  ))}
                </Stack>
                <Text size="xs" c="gray.5" fs="italic" style={{ fontFamily: "var(--font-serif)" }}>
                  Exactly — chlorophyll absorbs light most strongly in the blue and red bands.
                </Text>
              </Stack>
            </Paper>
          </Box>
        </Group>
      </Container>

      {/* Feature row */}
      <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 72 }}>
        <Stack gap={32}>
          <Title
            order={2}
            ta="center"
            style={{
              fontFamily: "var(--font-serif), Georgia, serif",
              fontWeight: 500,
              fontSize: "clamp(1.5rem, 3vw, 2.1rem)",
              letterSpacing: "-0.01em",
            }}
          >
            Study the way understanding is built.
          </Title>
          <Group gap="lg" grow align="stretch">
            {FEATURES.map((f) => (
              <Paper key={f.title} radius="lg" p="xl" shadow="paper" bg="gray.0">
                <Stack gap={14}>
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
                  <Title order={4} style={{ fontFamily: "var(--font-serif), Georgia, serif", fontWeight: 500 }}>
                    {f.title}
                  </Title>
                  <Text size="sm" c="gray.6" lh={1.6}>
                    {f.body}
                  </Text>
                </Stack>
              </Paper>
            ))}
          </Group>
        </Stack>
      </Container>

      {/* Final CTA */}
      <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 80 }}>
        <Paper radius="xl" p={{ base: "xl", md: 56 }} shadow="paper-lg" bg="gray.8" style={{ overflow: "hidden" }}>
          <Stack align="center" gap={20} ta="center">
            <Box
              style={{
                width: 52,
                height: 52,
                borderRadius: "var(--mantine-radius-md)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "rgba(244, 241, 233, 0.12)",
                color: "var(--mantine-color-gray-1)",
              }}
            >
              <IconBook2 size={26} stroke={1.6} />
            </Box>
            <Title
              order={2}
              c="gray.0"
              maw={620}
              style={{
                fontFamily: "var(--font-serif), Georgia, serif",
                fontWeight: 500,
                fontSize: "clamp(1.6rem, 3.5vw, 2.3rem)",
                letterSpacing: "-0.01em",
                lineHeight: 1.2,
              }}
            >
              Open a book. Ask a question. Know what you understand.
            </Title>
            <Text size="md" c="gray.3" maw={520} lh={1.6}>
              No setup, no credit card. Upload a source and start studying in under a minute.
            </Text>
            <Center>
              <Button
                size="lg"
                variant="white"
                component={Link}
                href="/workspace"
                rightSection={<IconArrowRight size={18} stroke={1.75} />}
              >
                Start studying
              </Button>
            </Center>
          </Stack>
        </Paper>
      </Container>

      {/* Footer */}
      <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "lg", md: 32 }}>
        <Group justify="space-between" wrap="nowrap" align="center">
          <BrandMark height={24} />
          <Text size="xs" c="gray.5">
            © {new Date().getFullYear()} Zivo. Question better.
          </Text>
        </Group>
      </Container>
    </Box>
  );
}
