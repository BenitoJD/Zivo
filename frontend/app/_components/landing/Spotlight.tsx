"use client";

import { Box, Container, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { IconSparkles, IconUser } from "@tabler/icons-react";
import { Reveal } from "./motion";

/**
 * One editorial deep-dive: prose left, a calm mini chat-mock right. Adds depth
 * beyond the card grid - the Linear/Vercel "feature spotlight" rhythm.
 */
export function Spotlight() {
  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 80 }}>
      <Paper
        radius="xl"
        p={{ base: "xl", md: 48 }}
        bg="gray.0"
        shadow="paper"
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
            background: "radial-gradient(circle 400px at 90% 80%, rgba(123, 93, 166, 0.05), transparent 70%)",
          }}
        />
        <Group align="center" wrap="wrap" gap="xl" style={{ position: "relative", zIndex: 1 }}>
          <Stack gap={20} maw={520}>
            <Reveal>
              <Text size="xs" fw={600} tt="uppercase" lts={2} c="lavender.7">
                Grounded, not invented
              </Text>
            </Reveal>
            <Reveal>
              <Title
                order={2}
                style={{
                  fontFamily: "var(--font-sans), sans-serif",
                  fontWeight: 500,
                  fontSize: "clamp(1.6rem, 3vw, 2.2rem)",
                  letterSpacing: "-0.015em",
                  lineHeight: 1.18,
                }}
              >
                A tutor that answers from{" "}
                <Box component="span" c="lavender.7" style={{ fontWeight: 700 }}>
                  your source
                </Box>
                , not the internet.
              </Title>
            </Reveal>
            <Reveal>
              <Text size="md" c="gray.6" lh={1.7}>
                Every reply is grounded in the exact pages you uploaded. Ask
                &ldquo;why?&rdquo; or &ldquo;explain that again&rdquo; in plain
                language - Zivo cites the passage, not a guess. So you learn what
                the material actually says.
              </Text>
            </Reveal>
          </Stack>

          <Box visibleFrom="md">
            <Reveal>
              <Paper
                radius="lg"
                p="lg"
                bg="var(--mantine-color-body)"
                withBorder
                shadow="paper"
              >
                <Stack gap={16}>
                  <ChatBubble role="user" text="Why is price equal to marginal revenue here?" />
                  <ChatBubble
                    role="tutor"
                    text="Because each extra unit sells at the market price, so the revenue from one more unit equals that price - see page 12."
                  />
                </Stack>
              </Paper>
            </Reveal>
          </Box>
        </Group>
      </Paper>
    </Container>
  );
}

function ChatBubble({ role, text }: { role: "user" | "tutor"; text: string }) {
  const isTutor = role === "tutor";
  return (
    <Group gap={10} wrap="nowrap" align="flex-start">
      <Box
        style={{
          width: 32,
          height: 32,
          borderRadius: "var(--mantine-radius-md)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          background: isTutor
            ? "var(--mantine-color-lavender-0)"
            : "var(--mantine-color-gray-1)",
          color: isTutor
            ? "var(--mantine-color-lavender-7)"
            : "var(--mantine-color-gray-7)",
        }}
      >
        {isTutor ? <IconSparkles size={16} stroke={1.7} /> : <IconUser size={16} stroke={1.7} />}
      </Box>
      <Stack gap={2} style={{ minWidth: 0 }}>
        <Text size="xs" fw={600} tt="uppercase" lts={1} c={isTutor ? "lavender.7" : "gray.5"}>
          {isTutor ? "Zivo" : "You"}
        </Text>
        <Text
          size="sm"
          c={isTutor ? "gray.8" : "gray.7"}
          lh={1.55}
          fs={isTutor ? undefined : "italic"}
          style={isTutor ? undefined : { fontFamily: "var(--font-serif)" }}
        >
          {text}
        </Text>
      </Stack>
    </Group>
  );
}
