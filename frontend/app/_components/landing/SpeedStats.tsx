"use client";

import { Box, Container, Group, Stack, Text } from "@mantine/core";
import { motion, useReducedMotion } from "framer-motion";
import { Reveal, EASE } from "./motion";

type Bar = {
  label: string;
  value: string;
  /** Bar fill as a fraction of the track width. */
  pct: number;
  accent: boolean;
};

const BARS: Bar[] = [
  { label: "Re-reading your notes", value: "~20% recall", pct: 0.22, accent: false },
  { label: "Testing yourself with Zivo", value: "~80% recall", pct: 0.85, accent: true },
];

/**
 * Wispr-Flow-style stat band - their "4x faster than typing" with the animated
 * Keyboard-vs-Flow bars, reframed for Zivo: passive re-reading vs. active recall.
 * Bars grow from zero when scrolled into view (the satisfying Wispr reveal).
 */
export function SpeedStats() {
  const reduce = useReducedMotion();

  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: 56, md: 88 }}>
      <Stack align="center" gap={48}>
        <Stack align="center" gap={14} ta="center" maw={680}>
          <Reveal>
            <Text size="xs" fw={600} tt="uppercase" lts={2} c="lavender.7">
              The science of remembering
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
              Remember{" "}
              <Box component="span" style={{ color: "var(--mantine-color-lavender-7)" }}>
                4&times; more
              </Box>{" "}
              by answering, not re-reading.
            </Box>
          </Reveal>
          <Reveal>
            <Text size="lg" c="gray.6" lh={1.6} maw={560}>
              Decades of learning research are blunt about it: retrieving an answer
              cements memory far better than reviewing the same page again.
            </Text>
          </Reveal>
        </Stack>

        <Reveal>
          <Box
            style={{
              width: "min(720px, 100%)",
              background: "var(--mantine-color-gray-0)",
              border: "1px solid var(--mantine-color-default-border)",
              borderRadius: "var(--mantine-radius-xl)",
              padding: "clamp(20px, 4vw, 40px)",
              boxShadow: "var(--mantine-shadow-paper)",
            }}
          >
            <Stack gap={28}>
              {BARS.map((bar) => (
                <Stack key={bar.label} gap={10}>
                  <Group justify="space-between" wrap="nowrap">
                    <Text
                      size="sm"
                      fw={600}
                      c={bar.accent ? "lavender.7" : "gray.7"}
                      style={{ fontFamily: "var(--font-sans)" }}
                    >
                      {bar.label}
                    </Text>
                    <Text
                      size="sm"
                      fw={700}
                      c={bar.accent ? "lavender.7" : "gray.5"}
                      style={{ fontFamily: "var(--font-sans)", fontVariantNumeric: "tabular-nums" }}
                    >
                      {bar.value}
                    </Text>
                  </Group>
                  <Box
                    style={{
                      height: 16,
                      borderRadius: 999,
                      background: "var(--mantine-color-gray-2)",
                      overflow: "hidden",
                    }}
                  >
                    <motion.div
                      initial={reduce ? false : { width: 0 }}
                      whileInView={{ width: `${bar.pct * 100}%` }}
                      viewport={{ once: true, margin: "-15% 0px -15% 0px" }}
                      transition={{ duration: 1.1, ease: EASE, delay: 0.1 }}
                      style={{
                        height: "100%",
                        borderRadius: 999,
                        background: bar.accent
                          ? "linear-gradient(90deg, var(--mantine-color-lavender-6), var(--mantine-color-lavender-8))"
                          : "var(--mantine-color-gray-4)",
                      }}
                    />
                  </Box>
                </Stack>
              ))}
            </Stack>
          </Box>
        </Reveal>
      </Stack>
    </Container>
  );
}
