"use client";

/**
 * Public coding problem solve page — full-viewport LeetCode-style IDE.
 */

import { use, useEffect } from "react";
import { Box, Button, Group, Stack, Text } from "@mantine/core";
import { IconArrowLeft } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useCodingProblemQuery } from "@/lib/api/queries";
import { CodeEditor } from "@/app/_components/coding/CodeEditor";
import { useMediaQuery } from "@mantine/hooks";
import { MOBILE_MAX_MQ } from "@/lib/responsive";

export default function CodingSolvePage({ params }: { params: Promise<{ id: string }> }) {
  const router = useRouter();
  const { id } = use(params);
  const { data: problem, isLoading, isError } = useCodingProblemQuery(id, Boolean(id));
  const compact = useMediaQuery(MOBILE_MAX_MQ, false, { getInitialValueInEffect: true });

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  if (!id) {
    return (
      <Box bg="var(--mantine-color-body)" h="100dvh" p="md">
        <Text c="dimmed">No problem id.</Text>
      </Box>
    );
  }

  if (isLoading) {
    return (
      <Box bg="var(--mantine-color-body)" h="100dvh" p="md">
        <Button
          variant="subtle"
          size="xs"
          leftSection={<IconArrowLeft size={14} />}
          onClick={() => router.push("/practice/coding")}
          mb="sm"
        >
          All problems
        </Button>
        <Text c="dimmed">Loading problem…</Text>
      </Box>
    );
  }

  if (isError || !problem) {
    return (
      <Box bg="var(--mantine-color-body)" h="100dvh" p="md">
        <Stack gap="md">
          <Text c="terracotta.7">This problem couldn&rsquo;t be loaded.</Text>
          <Button variant="light" leftSection={<IconArrowLeft size={16} />} onClick={() => router.push("/practice/coding")}>
            Back to coding practice
          </Button>
        </Stack>
      </Box>
    );
  }

  return (
    <Box
      bg="var(--mantine-color-body)"
      h="100dvh"
      display="flex"
      style={{ flexDirection: "column", overflow: "hidden" }}
    >
      <Group
        px="sm"
        py={6}
        justify="space-between"
        style={{ borderBottom: "1px solid var(--mantine-color-default-border)", flexShrink: 0 }}
      >
        <Button
          variant="subtle"
          size="xs"
          leftSection={<IconArrowLeft size={14} />}
          onClick={() => router.push("/practice/coding")}
        >
          All problems
        </Button>
        <Text fz="xs" c="dimmed" lineClamp={1}>
          {problem.title}
        </Text>
      </Group>
      <Box flex={1} style={{ minHeight: 0 }}>
        <CodeEditor problem={problem} compact={Boolean(compact)} variant="ide" />
      </Box>
    </Box>
  );
}
