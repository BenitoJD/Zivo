"use client";

import Link from "next/link";
import { Box, Container, Group, Stack, Text } from "@mantine/core";
import { BrandMark } from "@/app/_components/BrandMark";

const COLUMNS: { heading: string; links: { label: string; href: string }[] }[] = [
  {
    heading: "Product",
    links: [
      { label: "Start studying", href: "/workspace" },
      { label: "Sign in", href: "/login" },
      { label: "Create account", href: "/signup" },
    ],
  },
  {
    heading: "Learn",
    links: [
      { label: "How it works", href: "/#how" },
      { label: "Questions", href: "/#faq" },
    ],
  },
];

/**
 * Calm footer: brand + tagline left, two quiet link columns right, and a
 * suppressed-hydration copyright line (dynamic year).
 */
export function Footer() {
  return (
    <Box
        component="footer"
        style={{
          borderTop: "1px solid var(--mantine-color-default-border)",
        }}
      >
      <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 40 }}>
        <Group
          align="flex-start"
          justify="space-between"
          gap={48}
          wrap="wrap"
        >
          <Stack gap={10} maw={300}>
            <BrandMark height={26} />
            <Text size="sm" c="gray.5" lh={1.55}>
              Measure and improve understanding through questions. One question at a time.
            </Text>
          </Stack>

          <Group gap={52} wrap="wrap" align="flex-start">
            {COLUMNS.map((col) => (
              <Stack key={col.heading} gap={10}>
                <Text size="xs" fw={700} tt="uppercase" lts={1.5} c="gray.5">
                  {col.heading}
                </Text>
                {col.links.map((l) => (
                  <Text
                    key={l.label}
                    component={Link}
                    href={l.href}
                    size="sm"
                    c="gray.7"
                    style={{ textDecoration: "none" }}
                  >
                    {l.label}
                  </Text>
                ))}
              </Stack>
            ))}
          </Group>
        </Group>

        <Group justify="space-between" mt={{ base: 32, md: 40 }} wrap="wrap" align="center" gap="xs">
          <Text size="xs" c="gray.5" suppressHydrationWarning>
            © {new Date().getFullYear()} Zivo. Question better.
          </Text>
          <Text size="xs" c="gray.5" suppressHydrationWarning>
            Made for the relentlessly curious.
          </Text>
        </Group>
      </Container>
    </Box>
  );
}
