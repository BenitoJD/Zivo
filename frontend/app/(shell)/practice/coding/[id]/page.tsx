"use client";

/**
 * Coding problem solve page — IDE inside the shared learner AppShell.
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

function Fill({ children, p }: { children: React.ReactNode; p?: string }) {
  return (
    <Box
      flex={1}
      mih={0}
      bg="var(--mantine-color-body)"
      p={p}
      display="flex"
      style={{ flexDirection: "column", overflow: "hidden" }}
    >
      {children}
    </Box>
  );
}

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
      <Fill p="md">
        <Text c="dimmed">No problem id.</Text>
      </Fill>
    );
  }

  if (isLoading) {
    return (
      <Fill p="md">
        <Button
          variant="subtle"
          size="xs"
          leftSection={<IconArrowLeft size={14} />}
          onClick={() => router.push("/practice/coding")}
          mb="sm"
          w="fit-content"
        >
          All problems
        </Button>
        <Text c="dimmed">Loading problem…</Text>
      </Fill>
    );
  }

  if (isError || !problem) {
    return (
      <Fill p="md">
        <Stack gap="md">
          <Text c="terracotta.7">This problem couldn&rsquo;t be loaded.</Text>
          <Button
            variant="light"
            leftSection={<IconArrowLeft size={16} />}
            onClick={() => router.push("/practice/coding")}
            w="fit-content"
          >
            Back to coding practice
          </Button>
        </Stack>
      </Fill>
    );
  }

  return (
    <Fill>
      <Group
        px="sm"
        py={6}
        justify="space-between"
        wrap="nowrap"
        gap="sm"
        style={{ borderBottom: "1px solid var(--mantine-color-default-border)", flexShrink: 0, minWidth: 0 }}
      >
        <Button
          variant="subtle"
          size="xs"
          leftSection={<IconArrowLeft size={14} />}
          onClick={() => router.push("/practice/coding")}
          style={{ flexShrink: 0 }}
        >
          All problems
        </Button>
        <Text fz="xs" c="dimmed" lineClamp={1} style={{ minWidth: 0 }}>
          {problem.title}
        </Text>
      </Group>
      <Box flex={1} style={{ minHeight: 0 }}>
        <CodeEditor problem={problem} compact={Boolean(compact)} variant="ide" />
      </Box>
    </Fill>
  );
}
