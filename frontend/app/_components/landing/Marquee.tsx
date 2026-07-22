"use client";

import { Box, Container, Stack, Text } from "@mantine/core";
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
  { icon: IconFileText, label: "PDFs", c: "terracotta" },
  { icon: IconPresentation, label: "Lecture slides", c: "lavender" },
  { icon: IconArticle, label: "Articles", c: "forest" },
  { icon: IconNotebook, label: "Your notes", c: "sage" },
  { icon: IconBrandGithub, label: "GitHub repos", c: "gray" },
  { icon: IconMicrophone, label: "Transcripts", c: "lavender" },
] as const;

const CHIP_STYLES = `
  .zivo-mq-chip {
    display: inline-flex;
    align-items: center;
    gap: 9px;
    padding: 7px 16px 7px 7px;
    border-radius: 999px;
    background: var(--mantine-color-gray-0);
    border: 1px solid var(--mantine-color-default-border);
    box-shadow: 0 2px 8px rgba(35, 34, 32, 0.05), 0 1px 2px rgba(35, 34, 32, 0.04);
    white-space: nowrap;
    list-style: none;
  }
  .zivo-mq-ico {
    width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
  }
`;

function Chip({ item }: { item: (typeof ITEMS)[number] }) {
  const Icon = item.icon;
  return (
    <Box component="li" className="zivo-mq-chip">
      <span
        className="zivo-mq-ico"
        style={{
          background: `var(--mantine-color-${item.c}-0)`,
          color: `var(--mantine-color-${item.c}-7)`,
        }}
      >
        <Icon size={16} stroke={1.8} />
      </span>
      <Text size="sm" fw={600} c="var(--mantine-color-text)" component="span">
        {item.label}
      </Text>
    </Box>
  );
}

// The 6 source types repeated so a single row is always wider than the viewport
// (even on ultra-wide screens) - otherwise the loop scrolls past the content and
// leaves empty space on the right before it repeats.
const ROW_REPEAT = 4;

/** One marquee row (items repeated to fill the width). Duplicated for the loop.
 *  paddingRight gives the trailing gap so the seam between the two rows matches
 *  the inter-chip gap exactly - and translateX(-50%) lands one full row over. */
function MarqueeRow() {
  return (
    <Box
      component="ul"
      style={{
        display: "flex",
        gap: 18,
        paddingRight: 18,
        alignItems: "center",
        listStyle: "none",
        margin: 0,
        flexShrink: 0,
      }}
    >
      {Array.from({ length: ROW_REPEAT }).flatMap((_, r) =>
        ITEMS.map((it) => <Chip key={`${it.label}-${r}`} item={it} />),
      )}
    </Box>
  );
}

/**
 * Infinite "works with" strip - premium pill chips with colored source-type tiles,
 * scrolling seamlessly. Pauses on hover; renders statically under reduced-motion.
 */
export function Marquee() {
  const reduce = useReducedMotion();

  if (reduce) {
    return (
      <Container size="lg" px={{ base: "md", md: "lg" }} py="md">
        <style>{CHIP_STYLES}</style>
        <Stack align="center" gap={14}>
          <Text size="xs" fw={600} tt="uppercase" lts={2} c="dimmed">
            Works with whatever you study
          </Text>
          <Box
            component="ul"
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 14,
              justifyContent: "center",
              listStyle: "none",
              margin: 0,
              padding: 0,
            }}
          >
            {ITEMS.map((it) => (
              <Chip key={it.label} item={it} />
            ))}
          </Box>
        </Stack>
      </Container>
    );
  }

  return (
    <Box py="md">
      <style>{CHIP_STYLES}</style>
      <Container size="lg" px={{ base: "md", md: "lg" }} pb="md">
        <Text size="xs" fw={600} tt="uppercase" lts={2} c="dimmed" ta="center">
          Works with whatever you study
        </Text>
      </Container>
      <Box
        style={{
          overflow: "hidden",
          // Wide edge fade so chips dissolve well before the edge - the loop seam
          // (and the duplicated row) is never visible.
          WebkitMaskImage:
            "linear-gradient(to right, transparent 0%, #000 16%, #000 84%, transparent 100%)",
          maskImage:
            "linear-gradient(to right, transparent 0%, #000 16%, #000 84%, transparent 100%)",
        }}
      >
        <Box
          style={{
            display: "flex",
            width: "max-content",
            // ~same px/s as before, but the row is ROW_REPEAT× wider, so scale
            // the duration to keep the scroll speed natural.
            animation: "zivo-marquee 120s linear infinite",
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
