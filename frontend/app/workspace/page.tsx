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
        <Paper
          radius="xl"
          p={{ base: "xl", md: 48 }}
          shadow="paper-lg"
          bg="gray.0"
          w="100%"
          style={{
            transition: "transform 280ms cubic-bezier(0.32, 0.72, 0, 1), box-shadow 280ms cubic-bezier(0.32, 0.72, 0, 1)",
            border: "1px solid var(--mantine-color-default-border)",
          }}
          className="hero-welcome-card"
        >
          <Stack align="center" gap={20} ta="center">
            <ThemeIcon
              size={60}
              radius="xl"
              variant="light"
              color="lavender"
              style={{
                background: "var(--mantine-color-lavender-0)",
                border: "1px solid var(--mantine-color-lavender-2)",
              }}
            >
              <IconBookUpload size={30} stroke={1.6} style={{ color: "var(--mantine-color-lavender-7)" }} />
            </ThemeIcon>
            <Stack gap={10} align="center">
              <Title
                order={2}
                ta="center"
                style={{
                  fontFamily: "var(--font-serif), 'EB Garamond', Georgia, serif",
                  fontWeight: 500,
                  letterSpacing: "-0.015em",
                  fontSize: "clamp(1.7rem, 3.2vw, 2.2rem)",
                  color: "var(--mantine-color-text)",
                }}
              >
                Your library is quiet.
              </Title>
              <Text c="gray.6" ta="center" size="md" lh={1.6} maw={450} style={{ fontFamily: "var(--font-sans), sans-serif" }}>
                Add a source to begin. Upload anything you&apos;re studying — Zivo will turn it into
                questions that actually test your understanding.
              </Text>
            </Stack>
            <Button
              size="lg"
              onClick={openAddSource}
              rightSection={<IconArrowRight size={18} stroke={1.75} />}
              style={{
                transition: "transform 150ms ease, box-shadow 150ms ease",
                fontFamily: "var(--font-sans), sans-serif",
                fontWeight: 600,
              }}
              className="workspace-cta-button"
            >
              Add a source
            </Button>
            
            {/* Tag chip formats */}
            <Group gap="xs" justify="center" mt="sm" wrap="wrap">
              {["PDF", "Word", "URL", "Paste", "GitHub"].map((tag) => (
                <Box
                  key={tag}
                  style={{
                    padding: "4px 10px",
                    borderRadius: "8px",
                    border: "1px solid var(--mantine-color-default-border)",
                    background: "var(--mantine-color-default-hover)",
                    fontSize: "10px",
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    color: "var(--mantine-color-gray-6)",
                    fontFamily: "var(--font-sans), sans-serif",
                  }}
                >
                  {tag}
                </Box>
              ))}
            </Group>
          </Stack>
        </Paper>

        {/* How it works */}
        <Group grow align="stretch" gap="md" w="100%" mt="lg" visibleFrom="sm">
          {STEPS.map((s, i) => (
            <Paper
              key={s.title}
              radius="lg"
              p="lg"
              shadow="paper"
              withBorder
              bg="gray.0"
              style={{
                transition: "transform 280ms cubic-bezier(0.32, 0.72, 0, 1), box-shadow 280ms cubic-bezier(0.32, 0.72, 0, 1)",
                cursor: "default",
              }}
              className="step-card"
            >
              <Stack gap={12}>
                <Group gap={10} align="center">
                  <ThemeIcon
                    size={36}
                    radius="md"
                    variant="light"
                    color="lavender"
                    style={{
                      background: "var(--mantine-color-lavender-0)",
                      border: "1px solid var(--mantine-color-lavender-2)",
                    }}
                  >
                    <s.icon size={18} stroke={1.6} style={{ color: "var(--mantine-color-lavender-7)" }} />
                  </ThemeIcon>
                  <Text size="xs" c="gray.5" fw={700} tt="uppercase" lts={1} style={{ fontFamily: "var(--font-sans), sans-serif" }}>
                    Step {i + 1}
                  </Text>
                </Group>
                <Text
                  fw={600}
                  size="sm"
                  style={{
                    fontFamily: "var(--font-serif), 'EB Garamond', Georgia, serif",
                    color: "var(--mantine-color-text)",
                  }}
                >
                  {s.title}
                </Text>
                <Text size="xs" c="gray.6" lh={1.55} style={{ fontFamily: "var(--font-sans), sans-serif" }}>
                  {s.body}
                </Text>
              </Stack>
            </Paper>
          ))}
        </Group>
      </Stack>

      <style>{`
        .hero-welcome-card:hover {
          transform: translateY(-3px);
          box-shadow: var(--mantine-shadow-paper-lg) !important;
        }
        .step-card:hover {
          transform: translateY(-2px);
          box-shadow: var(--mantine-shadow-paper-lg) !important;
        }
        .workspace-cta-button:hover {
          transform: translateY(-1px);
        }
      `}</style>
    </Center>
  );
}
