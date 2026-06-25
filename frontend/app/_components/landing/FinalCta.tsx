"use client";

import Link from "next/link";
import { Box, Button, Center, Container, Paper, Stack, Text, Title } from "@mantine/core";
import { IconArrowRight, IconBook2 } from "@tabler/icons-react";
import { Reveal } from "./motion";

/**
 * Closing call-to-action on the dark ink panel — the one intentional dark
 * surface on the landing, echoing AuthSplitLayout's brand panel. Frosted icon
 * tile + serif headline + single primary (white) button.
 */
export function FinalCta() {
  return (
    <Container size="lg" px={{ base: "md", md: "lg" }} py={{ base: "xl", md: 88 }}>
      <Reveal>
        <Paper
          radius="xl"
          p={{ base: "xl", md: 64 }}
          bg="gray.8"
          shadow="paper-lg"
          style={{ overflow: "hidden", position: "relative" }}
        >
          <Stack align="center" gap={22} ta="center">
            <Box
              style={{
                width: 56,
                height: 56,
                borderRadius: "var(--mantine-radius-lg)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "rgba(244, 241, 233, 0.12)",
                color: "var(--mantine-color-gray-1)",
              }}
            >
              <IconBook2 size={26} stroke={1.6} />
            </Box>
            <Title
              order={2}
              c="gray.0"
              maw={640}
              style={{
                fontFamily: "var(--font-serif), Georgia, serif",
                fontWeight: 500,
                fontSize: "clamp(1.7rem, 3.6vw, 2.5rem)",
                letterSpacing: "-0.02em",
                lineHeight: 1.2,
              }}
            >
              Open a book. Ask a question. Know what you understand.
            </Title>
            <Text size="md" c="gray.3" maw={540} lh={1.6}>
              No setup, no credit card. Upload a source and start studying in
              under a minute.
            </Text>
            <Center>
              <Button
                size="lg"
                variant="white"
                component={Link}
                href="/workspace"
                rightSection={<IconArrowRight size={18} stroke={1.75} />}
              >
                Start studying
              </Button>
            </Center>
          </Stack>
        </Paper>
      </Reveal>
    </Container>
  );
}
