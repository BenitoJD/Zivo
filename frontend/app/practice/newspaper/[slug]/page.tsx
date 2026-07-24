"use client";

/**
 * Newspaper paper — pick a day from the rolling month.
 */

import { useEffect, use } from "react";
import {
  Anchor,
  Badge,
  Box,
  Button,
  Container,
  Group,
  Paper,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { IconArrowLeft, IconArrowRight } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useNewspaperDaysQuery } from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";

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

  return (
    <Shell>
      <Container size="sm" py={{ base: 36, md: 64 }}>
        <Stack gap="xl">
          <Anchor
            component="button"
            c="dimmed"
            fz="sm"
            onClick={() => router.push("/practice/newspaper")}
          >
            <Group gap={6}>
              <IconArrowLeft size={14} />
              All papers
            </Group>
          </Anchor>

          <Box>
            <Title order={2} ff="var(--font-serif)" fw={500}>
              {daysQ.data?.paper_title || slug}
            </Title>
            <Text c="dimmed" fz="sm">
              Last month of practice days.
            </Text>
          </Box>

          {daysQ.isLoading ? (
            <Text c="dimmed">Loading days…</Text>
          ) : daysQ.isError ? (
            <Text c="terracotta">Couldn&rsquo;t load days.</Text>
          ) : (daysQ.data?.days.length ?? 0) === 0 ? (
            <Text c="dimmed">No editions in the window yet.</Text>
          ) : (
            <Stack gap="sm">
              {daysQ.data!.days.map((d) => {
                const ready = d.status === "ready";
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
                          {ready ? "Ready" : d.status === "indexing" || d.status === "pending" ? "Preparing" : d.status}
                        </Badge>
                      </Group>
                      <Button
                        radius="xl"
                        variant="light"
                        color="lavender"
                        disabled={!ready}
                        rightSection={<IconArrowRight size={16} />}
                        onClick={() => router.push(`/practice/newspaper/e/${d.id}`)}
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
