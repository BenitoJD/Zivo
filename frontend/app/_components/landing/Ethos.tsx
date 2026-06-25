"use client";

import { Box, Container, Stack, Text, Title } from "@mantine/core";
import { Reveal } from "./motion";

/**
 * Honest, premium "ethos" band — no fabricated testimonials. A large serif
 * statement on why questions matter (ties to the product vision), plus a quiet
 * audience line. This is the calm counterpart to Wispr Flow's testimonial wall.
 */
export function Ethos() {
  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 96 }}>
      <Stack align="center" gap={20} ta="center" maw={820} mx="auto">
        <Reveal>
          <Text size="xs" fw={600} tt="uppercase" lts={2} c="lavender.7">
            Why questions
          </Text>
        </Reveal>
        <Reveal>
          <Title
            order={2}
            style={{
              fontFamily: "var(--font-serif), Georgia, serif",
              fontWeight: 500,
              fontSize: "clamp(1.9rem, 4vw, 2.9rem)",
              letterSpacing: "-0.02em",
              lineHeight: 1.2,
            }}
          >
            Questions are how understanding{" "}
            <Box
              component="span"
              fs="italic"
              c="lavender.7"
              style={{ fontFamily: "var(--font-serif), Georgia, serif" }}
            >
              reveals itself.
            </Box>
          </Title>
        </Reveal>
        <Reveal>
          <Text size="lg" c="gray.6" lh={1.7} maw={620}>
            You can re-read a chapter ten times and still not know what you
            don&apos;t know. A good question exposes that in seconds — and a good
            explanation closes the gap. Zivo exists to make that loop effortless,
            on anything you want to truly understand.
          </Text>
        </Reveal>
        <Reveal>
          <Text size="sm" c="gray.5" fs="italic" style={{ fontFamily: "var(--font-serif)" }}>
            Built for students, researchers, and the relentlessly curious.
          </Text>
        </Reveal>
      </Stack>
    </Container>
  );
}
