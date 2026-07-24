"use client";

/**
 * Newspaper door — pick a paper from the last month.
 * Practice hands off to Learn/Test at /workspace/[documentId].
 */

import { useEffect } from "react";
import {
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
import { useNewspaperCatalogQuery } from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";
import { PaperMasthead } from "@/app/practice/newspaper/_components/PaperMasthead";

export default function NewspaperDoorPage() {
  const router = useRouter();
  const catalog = useNewspaperCatalogQuery();

  useEffect(() => {
    void ensureGuestSession();
  }, []);

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
            onClick={() => router.push("/practice")}
          >
            Practice
          </Button>

          <PaperMasthead
            title="Today’s papers"
            subtitle="Pick a paper. Pick a day. Same Learn flow — questions only."
          />

          {catalog.isLoading ? (
            <Text c="dimmed" ta="center">
              Loading papers…
            </Text>
          ) : catalog.isError ? (
            <Text c="terracotta" ta="center">
              Couldn&rsquo;t load newspapers right now.
            </Text>
          ) : (catalog.data?.papers.length ?? 0) === 0 ? (
            <Paper radius="xl" p="xl" withBorder bg="gray.0" shadow="paper">
              <Stack gap="xs" ta="center">
                <Text ff="var(--font-serif)" fw={500} fz="lg">
                  Nothing ready yet
                </Text>
                <Text c="dimmed" size="sm">
                  Editions appear here once today&rsquo;s papers are cooked. Check back soon.
                </Text>
              </Stack>
            </Paper>
          ) : (
            <Stack gap="sm">
              {catalog.data!.papers.map((p) => (
                <Paper
                  key={p.slug}
                  radius="xl"
                  p="lg"
                  withBorder
                  bg="gray.0"
                  shadow="paper"
                >
                  <Group justify="space-between" align="center" wrap="wrap" gap="sm">
                    <Box style={{ minWidth: 0, flex: "1 1 180px" }}>
                      <Text fw={600} ff="var(--font-serif)" fz="lg" style={{ letterSpacing: "-0.01em" }}>
                        {p.title}
                      </Text>
                      <Text size="sm" c="dimmed">
                        {p.ready_days} day{p.ready_days === 1 ? "" : "s"} ready
                        {p.latest_date ? ` · latest ${p.latest_date}` : ""}
                      </Text>
                    </Box>
                    <Button
                      radius="xl"
                      variant="light"
                      color="lavender"
                      rightSection={<IconArrowRight size={16} />}
                      onClick={() => router.push(`/practice/newspaper/${p.slug}`)}
                    >
                      Open
                    </Button>
                  </Group>
                </Paper>
              ))}
            </Stack>
          )}
        </Stack>
      </Container>
    </Shell>
  );
}
