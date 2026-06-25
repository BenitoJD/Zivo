"use client";

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
} from "@mantine/core";
import {
  IconArrowRight,
  IconBookUpload,
  IconBulb,
  IconMessageCircle,
  IconUpload,
} from "@tabler/icons-react";
import { useWorkspaceShell } from "@/app/workspace/layout";

const STEPS = [
  { icon: IconUpload, title: "Upload", body: "A PDF, article, or your own notes." },
  { icon: IconBulb, title: "Practice", body: "Exam-style questions, scoped to what you read." },
  { icon: IconMessageCircle, title: "Ask", body: "A tutor that answers from your source." },
];

export default function WorkspaceIndexPage() {
  const { openAddSource } = useWorkspaceShell();

  return (
    <Center flex={1} px="sm" py="md" bg="var(--mantine-color-body)">
      <Stack align="center" gap={0} w="100%" maw={620}>
        {/* Hero welcome card */}
        <Paper radius="xl" p={{ base: "xl", md: 48 }} shadow="paper-lg" bg="gray.0" w="100%">
          <Stack align="center" gap={18} ta="center">
            <ThemeIcon
              size={56}
              radius="xl"
              variant="light"
              color="lavender"
            >
              <IconBookUpload size={28} stroke={1.5} />
            </ThemeIcon>
            <Stack gap={8} align="center">
              <Title
                order={2}
                ta="center"
                style={{
                  fontFamily: "var(--font-serif), Georgia, serif",
                  fontWeight: 500,
                  letterSpacing: "-0.01em",
                  fontSize: "clamp(1.6rem, 3vw, 2.1rem)",
                }}
              >
                Your library is quiet.
              </Title>
              <Text c="gray.6" ta="center" size="md" lh={1.6} maw={440}>
                Add a source to begin. Upload anything you&apos;re studying — Zivo will turn it into
                questions that actually test your understanding.
              </Text>
            </Stack>
            <Button
              size="lg"
              onClick={openAddSource}
              rightSection={<IconArrowRight size={18} stroke={1.75} />}
            >
              Add a source
            </Button>
            <Group gap={6} justify="center" mt={4}>
              {["PDF", "Word", "URL", "Paste", "GitHub"].map((tag, i) => (
                <Group gap={6} key={tag} wrap="nowrap">
                  {i > 0 && (
                    <Text size="xs" c="gray.4">
                      ·
                    </Text>
                  )}
                  <Text size="xs" c="gray.5" tt="uppercase" fw={600} lts={0.5}>
                    {tag}
                  </Text>
                </Group>
              ))}
            </Group>
          </Stack>
        </Paper>

        {/* How it works */}
        <Group grow align="stretch" gap="md" w="100%" mt="lg" visibleFrom="sm">
          {STEPS.map((s, i) => (
            <Paper key={s.title} radius="lg" p="lg" shadow="paper" withBorder>
              <Stack gap={10}>
                <Group gap={10} align="center">
                  <ThemeIcon size={36} radius="md" variant="light" color="lavender">
                    <s.icon size={18} stroke={1.6} />
                  </ThemeIcon>
                  <Text size="xs" c="gray.5" fw={700} tt="uppercase" lts={1}>
                    Step {i + 1}
                  </Text>
                </Group>
                <Text fw={600} size="sm" style={{ fontFamily: "var(--font-serif), Georgia, serif" }}>
                  {s.title}
                </Text>
                <Text size="xs" c="gray.6" lh={1.55}>
                  {s.body}
                </Text>
              </Stack>
            </Paper>
          ))}
        </Group>
      </Stack>
    </Center>
  );
}
