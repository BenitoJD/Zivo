"use client";

import { Box, Container, Stack, Text } from "@mantine/core";
import { useReducedMotion } from "framer-motion";

/**
 * Wispr-Flow-style social-proof strip — "Used by ... everywhere". We don't ship
 * fabricated company logos; instead this is an honest marquee of the exams and
 * fields learners prep for, rendered as muted wordmarks that scroll seamlessly.
 */
const LABELS = [
  "MCAT",
  "USMLE",
  "Bar Exam",
  "CFA",
  "GRE",
  "AP Bio",
  "NCLEX",
  "GMAT",
  "PE Exam",
  "A-Levels",
];

function Row() {
  return (
    <Box
      component="ul"
      style={{
        display: "flex",
        gap: 48,
        paddingRight: 48,
        alignItems: "center",
        listStyle: "none",
        margin: 0,
        flexShrink: 0,
      }}
    >
      {LABELS.map((label) => (
        <Box
          component="li"
          key={label}
          style={{
            fontFamily: "var(--font-sans), sans-serif",
            fontWeight: 700,
            fontSize: 20,
            letterSpacing: "-0.01em",
            color: "var(--mantine-color-gray-5)",
            whiteSpace: "nowrap",
          }}
        >
          {label}
        </Box>
      ))}
    </Box>
  );
}

export function LogoStrip() {
  const reduce = useReducedMotion();

  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: 28, md: 44 }}>
      <Stack gap={20}>
        <Text size="sm" fw={500} c="gray.6" ta="center">
          Used by learners preparing for the world&apos;s hardest exams
        </Text>

        {reduce ? (
          <Box
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 40,
              justifyContent: "center",
            }}
          >
            <Row />
          </Box>
        ) : (
          <Box
            style={{
              overflow: "hidden",
              WebkitMaskImage:
                "linear-gradient(to right, transparent, #000 8%, #000 92%, transparent)",
              maskImage:
                "linear-gradient(to right, transparent, #000 8%, #000 92%, transparent)",
            }}
          >
            <Box
              style={{
                display: "flex",
                width: "max-content",
                animation: "zivo-logos 38s linear infinite",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.animationPlayState = "paused";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.animationPlayState = "running";
              }}
            >
              <Row />
              <Row />
            </Box>
          </Box>
        )}
      </Stack>
      <style>{`@keyframes zivo-logos { from { transform: translateX(0); } to { transform: translateX(-50%); } }`}</style>
    </Container>
  );
}
