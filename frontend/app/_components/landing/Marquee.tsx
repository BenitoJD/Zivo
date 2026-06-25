"use client";

import { Box, Container, Group, Stack, Text } from "@mantine/core";
import { useReducedMotion } from "framer-motion";
import {
  IconFileText,
  IconPresentation,
  IconArticle,
  IconNotebook,
  IconBrandGithub,
  IconMicrophone,
} from "@tabler/icons-react";

const ITEMS = [
  { icon: IconFileText, label: "PDFs" },
  { icon: IconPresentation, label: "Lecture slides" },
  { icon: IconArticle, label: "Articles" },
  { icon: IconNotebook, label: "Your notes" },
  { icon: IconBrandGithub, label: "GitHub repos" },
  { icon: IconMicrophone, label: "Transcripts" },
];

/** A single row of the marquee. Duplicated for a seamless loop. */
function MarqueeRow() {
  return (
    <Box
      component="ul"
      style={{
        display: "flex",
        gap: 56,
        paddingRight: 56,
        alignItems: "center",
        listStyle: "none",
        margin: 0,
        padding: 0,
        flexShrink: 0,
      }}
    >
      {ITEMS.map((it) => (
        <Box component="li" key={it.label} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <it.icon size={16} stroke={1.6} />
          <Text
            size="sm"
            c="gray.6"
            fw={500}
            component="span"
            style={{ whiteSpace: "nowrap" }}
          >
            {it.label}
          </Text>
        </Box>
      ))}
    </Box>
  );
}

/**
 * Infinite "works with" strip. Uses a duplicated row translated by -50% under a
 * CSS keyframe, looped seamlessly. Pauses on hover. When reduced-motion is set,
 * the strip renders statically (centered, no animation).
 */
export function Marquee() {
  const reduce = useReducedMotion();

  if (reduce) {
    return (
      <Container size="lg" px={{ base: "md", md: "lg" }} py="md">
        <Stack align="center" gap={10}>
          <Text size="xs" fw={600} tt="uppercase" lts={2} c="gray.5">
            Works with whatever you study
          </Text>
          <Group gap={28} wrap="nowrap" justify="center">
            {ITEMS.map((it) => (
              <Group key={it.label} gap={8} wrap="nowrap">
                <it.icon size={16} stroke={1.6} />
                <Text size="sm" c="gray.7" fw={500}>
                  {it.label}
                </Text>
              </Group>
            ))}
          </Group>
        </Stack>
      </Container>
    );
  }

  return (
    <Box py="md">
      <Container size="lg" px={{ base: "md", md: "lg" }} pb="sm">
        <Text size="xs" fw={600} tt="uppercase" lts={2} c="gray.5" ta="center">
          Works with whatever you study
        </Text>
      </Container>
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
            animation: "zivo-marquee 34s linear infinite",
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLElement).style.animationPlayState = "paused";
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLElement).style.animationPlayState = "running";
          }}
        >
          <MarqueeRow />
          <MarqueeRow />
        </Box>
      </Box>
      <style>{`@keyframes zivo-marquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }`}</style>
    </Box>
  );
}
