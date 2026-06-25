"use client";

import Link from "next/link";
import { Box, Container, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { IconArrowLeft } from "@tabler/icons-react";
import { BrandMark } from "@/app/_components/BrandMark";

/**
 * Split editorial layout for the auth pages: left brand panel with a serif
 * welcome line, right form card. Collapses to a single centered column on
 * mobile.
 */
export function AuthSplitLayout({
  children,
  eyebrow,
  quote,
  attribution,
}: {
  children: React.ReactNode;
  eyebrow: string;
  quote: string;
  attribution: string;
}) {
  return (
    <Box bg="var(--mantine-color-body)" style={{ minHeight: "100dvh" }}>
      <Group align="stretch" wrap="nowrap" gap={0} style={{ minHeight: "100dvh" }}>
        {/* Brand panel */}
        <Box
          visibleFrom="md"
          bg="gray.8"
          style={{ flex: "1 1 0", minHeight: "100dvh", position: "relative" }}
          p="xl"
        >
          <Stack justify="space-between" style={{ minHeight: "100dvh" }} gap={0}>
            <BrandMark height={28} />
            <Stack gap={20} maw={480}>
              <Text
                size="xs"
                fw={600}
                tt="uppercase"
                lts={2}
                c="gray.4"
              >
                {eyebrow}
              </Text>
              <Title
                order={2}
                c="gray.0"
                style={{
                  fontFamily: "var(--font-serif), Georgia, serif",
                  fontWeight: 500,
                  lineHeight: 1.25,
                  fontSize: "clamp(1.6rem, 2.5vw, 2.2rem)",
                }}
              >
                {quote}
              </Title>
              <Text size="sm" c="gray.4" fs="italic" style={{ fontFamily: "var(--font-serif)" }}>
                {attribution}
              </Text>
            </Stack>
            <Box />
          </Stack>
        </Box>

        {/* Form panel */}
        <Box
          bg="var(--mantine-color-body)"
          style={{ flex: "1 1 0", minHeight: "100dvh" }}
          p={{ base: "md", md: "xl" }}
        >
          <Stack align="center" justify="center" style={{ minHeight: "100dvh" }} gap="lg">
            <Container size="xs" w="100%" p={0}>
              <Group justify="space-between" mb="lg" hiddenFrom="md" wrap="nowrap">
                <BrandMark height={26} />
              </Group>

              <Group mb="xl" visibleFrom="md">
                <Text
                  size="sm"
                  c="gray.5"
                  component={Link}
                  href="/"
                  style={{ cursor: "pointer", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6 }}
                >
                  <IconArrowLeft size={14} stroke={1.75} />
                  Back to home
                </Text>
              </Group>

              <Paper radius="xl" p={{ base: "lg", md: "xl" }} shadow="paper-lg" bg="gray.0">
                {children}
              </Paper>
            </Container>
          </Stack>
        </Box>
      </Group>
    </Box>
  );
}
