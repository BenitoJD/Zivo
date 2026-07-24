"use client";

/**
 * Legacy edition URL → Learn workspace (same MCQ surface).
 * Catalog stays under /practice/newspaper; practice is /workspace/[documentId].
 * Test uses ?mode=test (docs/QUESTION_BUDGET_ENGINE.md §0).
 */

import { use, useEffect, useState } from "react";
import { Button, Center, Stack, Text } from "@mantine/core";
import { IconArrowLeft } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { apiGet, ensureGuestSession } from "@/lib/api/client";
import { Shell } from "@/app/practice/_components/Shell";

type Edition = {
  id: string;
  paper_slug: string;
  paper_title: string;
  edition_date: string;
  status: string;
  document_id: string | null;
};

export default function NewspaperEditionRedirect({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [backHref, setBackHref] = useState("/practice/newspaper");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await ensureGuestSession();
        const ed = await apiGet<Edition>(`/api/newspaper/editions/${id}`);
        if (cancelled) return;
        const days = ed.paper_slug
          ? `/practice/newspaper/${ed.paper_slug}`
          : "/practice/newspaper";
        setBackHref(days);
        if (ed.document_id && (ed.status === "ready" || ed.status === "indexing")) {
          router.replace(`/workspace/${ed.document_id}`);
          return;
        }
        router.replace(days);
      } catch {
        if (!cancelled) setError("Couldn’t open this edition.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, router]);

  return (
    <Shell>
      <Center mih="50vh" px="md">
        {error ? (
          <Stack align="center" gap="md">
            <Text c="terracotta">{error}</Text>
            <Button
              variant="light"
              color="lavender"
              radius="xl"
              leftSection={<IconArrowLeft size={16} />}
              onClick={() => router.push(backHref)}
            >
              Back to papers
            </Button>
          </Stack>
        ) : (
          <Text c="dimmed">Opening Learn…</Text>
        )}
      </Center>
    </Shell>
  );
}
