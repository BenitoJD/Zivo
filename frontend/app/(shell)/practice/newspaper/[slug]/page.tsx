"use client";

/**
 * Newspaper paper — pick a day (last 30 days), then hand off to Learn workspace.
 * Explicit secondary CTA opens Test (docs/QUESTION_BUDGET_ENGINE.md §0).
 */

import { useEffect, use } from "react";
import {
  Badge,
  Button,
  Container,
  Group,
  Paper,
  Stack,
  Text,
} from "@mantine/core";
import { IconArrowRight, IconBook2, IconClipboardList } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useNewspaperDaysQuery } from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";
import { LearnerPageHeader } from "@/app/_components/study/LearnerPageHeader";
import { NewspaperBrandMark } from "@/app/_components/newspaper/NewspaperBrandMark";

export default function NewspaperPaperPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const router = useRouter();
  const daysQ = useNewspaperDaysQuery(slug);

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  const title = daysQ.data?.paper_title || slug;

  return (
    <Shell>
      <Container size="md" py={{ base: 32, md: 56 }}>
        <Stack gap="lg">
          <Group gap="md" align="flex-start" wrap="wrap">
            <NewspaperBrandMark slug={slug} title={title} size={56} />
            <LearnerPageHeader
              align="left"
              compact
              eyebrow="Newspaper"
              title={title}
              subtitle="Last 30 days. Open a day to Learn. Test is optional."
            />
          </Group>

          {daysQ.isLoading ? (
            <Text c="dimmed">Loading days…</Text>
          ) : daysQ.isError ? (
            <Text c="terracotta">Couldn&rsquo;t load days.</Text>
          ) : (daysQ.data?.days.length ?? 0) === 0 ? (
            <Text c="dimmed">No editions in the last 30 days yet.</Text>
          ) : (
            <Stack gap="xs">
              {daysQ.data!.days.map((d) => {
                const ready = d.status === "ready";
                const canOpen = Boolean(d.document_id) && ready;
                return (
                  <Paper key={d.id} radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
                    <Group justify="space-between" align="center" wrap="wrap" gap="sm">
                      <Group gap="sm">
                        <Text fw={600} ff="var(--font-serif)">
                          {d.edition_date}
                        </Text>
                        <Badge
                          variant="light"
                          color={ready ? "sage" : d.status === "failed" ? "terracotta" : "lavender"}
                          radius="xl"
                        >
                          {ready
                            ? "Ready"
                            : d.status === "indexing" || d.status === "pending"
                              ? "Preparing"
                              : d.status}
                        </Badge>
                      </Group>
                      <Group gap="xs" wrap="wrap">
                        {d.has_blog && d.blog_href ? (
                          <Button
                            radius="xl"
                            variant="subtle"
                            color="lavender"
                            fullWidth
                            maw={{ base: "100%", xs: 180 }}
                            leftSection={<IconBook2 size={16} />}
                            onClick={() => router.push(d.blog_href!)}
                          >
                            Read analysis
                          </Button>
                        ) : null}
                        <Button
                          radius="xl"
                          variant="subtle"
                          color="gray"
                          disabled={!canOpen}
                          fullWidth
                          maw={{ base: "100%", xs: 180 }}
                          leftSection={<IconClipboardList size={16} />}
                          onClick={() => {
                            if (!d.document_id) return;
                            router.push(`/workspace/${d.document_id}?mode=test`);
                          }}
                        >
                          Test this edition
                        </Button>
                        <Button
                          radius="xl"
                          variant="light"
                          color="lavender"
                          disabled={!canOpen}
                          fullWidth
                          maw={{ base: "100%", xs: 180 }}
                          rightSection={<IconArrowRight size={16} />}
                          onClick={() => {
                            if (!d.document_id) return;
                            router.push(`/practice/newspaper/e/${d.id}`);
                          }}
                        >
                          Learn
                        </Button>
                      </Group>
                    </Group>
                  </Paper>
                );
              })}
            </Stack>
          )}
        </Stack>
      </Container>
    </Shell>
  );
}
