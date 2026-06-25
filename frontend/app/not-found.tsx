"use client";

import Link from "next/link";
import { Box, Button, Center, Container, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { IconArrowLeft } from "@tabler/icons-react";
import { BrandMark } from "@/app/_components/BrandMark";

export default function NotFound() {
  return (
    <Box bg="var(--mantine-color-body)" style={{ minHeight: "100dvh" }}>
      <Container size="lg" px={{ base: "md", md: "lg" }}>
        <Group justify="space-between" h={72} wrap="nowrap">
          <BrandMark height={28} />
        </Group>
      </Container>
      <Center style={{ minHeight: "calc(100dvh - 72px)" }} px="md" pb={80}>
        <Paper radius="xl" p={{ base: "xl", md: 56 }} shadow="paper" bg="gray.0" maw={520} w="100%">
          <Stack align="center" gap={16} ta="center">
            <Text
              size="xs"
              fw={600}
              tt="uppercase"
              lts={2}
              c="lavender.7"
            >
              Lost the page
            </Text>
            <Title
              order={1}
              style={{
                fontFamily: "var(--font-serif), Georgia, serif",
                fontWeight: 500,
                fontSize: "clamp(2rem, 5vw, 2.8rem)",
                letterSpacing: "-0.02em",
                lineHeight: 1.1,
              }}
            >
              This page wandered off.
            </Title>
            <Text size="md" c="gray.6" lh={1.6} maw={380}>
              The page you&apos;re looking for doesn&apos;t exist or may have moved. Let&apos;s get you back to
              studying.
            </Text>
            <Button
              component={Link}
              href="/workspace"
              size="md"
              leftSection={<IconArrowLeft size={16} stroke={1.75} />}
            >
              Back to the library
            </Button>
          </Stack>
        </Paper>
      </Center>
    </Box>
  );
}
