"use client";

/**
 * Newspaper door — pick a paper from the last month.
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
  ThemeIcon,
  Title,
} from "@mantine/core";
import { IconArrowRight, IconNews } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useNewspaperCatalogQuery } from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";

export default function NewspaperDoorPage() {
  const router = useRouter();
  const catalog = useNewspaperCatalogQuery();

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  return (
    <Shell>
      <Container size="sm" py={{ base: 36, md: 64 }}>
        <Stack gap="xl">
          <Group gap="sm" align="center">
            <ThemeIcon variant="light" color="lavender" size={44} radius="xl">
              <IconNews size={22} />
            </ThemeIcon>
            <Box>
              <Title order={2} ff="var(--font-serif)" fw={500}>
                Newspaper
              </Title>
              <Text c="dimmed" fz="sm">
                Pick a paper. Pick a day. Question better — no PDF clutter.
              </Text>
            </Box>
          </Group>

          {catalog.isLoading ? (
            <Text c="dimmed">Loading papers…</Text>
          ) : catalog.isError ? (
            <Text c="terracotta">Couldn&rsquo;t load newspapers right now.</Text>
          ) : (catalog.data?.papers.length ?? 0) === 0 ? (
            <Paper radius="xl" p="xl" withBorder bg="gray.0" shadow="paper">
              <Stack gap="xs">
                <Title order={3} ff="var(--font-serif)" fw={500}>
                  Nothing ready yet
                </Title>
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
                      <Text fw={600} ff="var(--font-serif)">
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
