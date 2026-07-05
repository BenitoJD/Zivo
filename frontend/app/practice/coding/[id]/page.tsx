"use client";

/**
 * Public coding problem solve page — one LeetCode-style problem, anonymous.
 *
 * Reuses the shared CodeEditor (same component as the workspace Coding study
 * mode). A guest session is bootstrapped so submits record against a guest
 * entity and the run/submit endpoints pass their CSRF/guest guard.
 */

import { useEffect, useState } from "react";
import { Box, Button, Container, Stack, Text } from "@mantine/core";
import { IconArrowLeft } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useCodingProblemQuery } from "@/lib/api/queries";
import { CodeEditor } from "@/app/_components/coding/CodeEditor";

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <Box
      bg="var(--mantine-color-body)"
      style={{ height: "100dvh", overflowY: "auto", overflowX: "hidden" }}
    >
      {children}
    </Box>
  );
}

export default function CodingSolvePage({ params }: { params: Promise<{ id: string }> }) {
  const router = useRouter();
  const id = useResolvedParam(params);
  const { data: problem, isLoading, isError } = useCodingProblemQuery(id, Boolean(id));

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  if (!id) {
    return (
      <Shell>
        <Container size="md" py="xl"><Text c="dimmed">No problem id.</Text></Container>
      </Shell>
    );
  }
  if (isLoading) {
    return (
      <Shell>
        <Container size="md" py="xl"><Text c="dimmed">Loading problem…</Text></Container>
      </Shell>
    );
  }
  if (isError || !problem) {
    return (
      <Shell>
        <Container size="md" py="xl">
          <Stack gap="md">
            <Text c="terracotta.7">This problem couldn&rsquo;t be loaded.</Text>
            <Button variant="light" leftSection={<IconArrowLeft size={16} />} onClick={() => router.push("/practice/coding")}>
              Back to coding practice
            </Button>
          </Stack>
        </Container>
      </Shell>
    );
  }

  return (
    <Shell>
      <Container size="md" py={{ base: 16, md: 28 }}>
        <Button
          variant="subtle"
          size="xs"
          leftSection={<IconArrowLeft size={14} />}
          onClick={() => router.push("/practice/coding")}
          mb="sm"
        >
          All problems
        </Button>
        <CodeEditor problem={problem} />
      </Container>
    </Shell>
  );
}

/** Unwrap Next.js 16 Promise params in a sync client component.
 *  Effect-based resolution avoids awaiting a promise during render. */
function useResolvedParam(params: Promise<{ id: string }>): string | null {
  const [id, setId] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    void params.then((p) => {
      if (alive) setId(p.id);
    });
    return () => {
      alive = false;
    };
  }, [params]);
  return id;
}
