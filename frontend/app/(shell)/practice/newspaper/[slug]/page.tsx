"use client";

/**
 * Newspaper paper — pick a day, then hand off to Learn/Test workspace.
 */

import { useEffect, use } from "react";
import {
  Badge,
  Box,
  Button,
  Container,
  Group,
  Paper,
  Stack,
  Text,
} from "@mantine/core";
import { IconArrowLeft, IconArrowRight } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useNewspaperDaysQuery } from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";
import { PaperMasthead } from "@/app/practice/newspaper/_components/PaperMasthead";

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
      <Container size="sm" py={{ base: 28, md: 56 }}>
        <Stack gap="xl">
          <Button
            variant="subtle"
            color="gray"
            size="compact-sm"
            w="fit-content"
            leftSection={<IconArrowLeft size={14} />}
            onClick={() => router.push("/practice/newspaper")}
          >
            All papers
          </Button>

          <PaperMasthead
            title={title}
            subtitle="Last month of practice days. Open a day to study in Learn."
          />

          {daysQ.isLoading ? (
            <Text c="dimmed" ta="center">
              Loading days…
            </Text>
          ) : daysQ.isError ? (
            <Text c="terracotta" ta="center">
              Couldn&rsquo;t load days.
            </Text>
          ) : (daysQ.data?.days.length ?? 0) === 0 ? (
            <Text c="dimmed" ta="center">
              No editions in the window yet.
            </Text>
          ) : (
            <Stack gap="sm">
              {daysQ.data!.days.map((d) => {
                const ready = d.status === "ready";
                const canOpen = Boolean(d.document_id) && (ready || d.status === "indexing");
                return (
                  <Paper key={d.id} radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
                    <Group justify="space-between" align="center" wrap="wrap" gap="sm">
                      <Group gap="sm">
                        <Text fw={600} ff="var(--font-serif)" fz="lg" style={{ letterSpacing: "-0.01em" }}>
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
                      <Button
                        radius="xl"
                        variant="light"
                        color="lavender"
                        disabled={!canOpen}
                        rightSection={<IconArrowRight size={16} />}
                        onClick={() => {
                          if (!d.document_id) return;
                          router.push(`/workspace/${d.document_id}`);
                        }}
                      >
                        Practice
                      </Button>
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
