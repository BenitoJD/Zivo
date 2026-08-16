"use client";

import { pick, choose } from "@/lib/engineRuntime";
/**
 * Newspaper door — pick a paper from the last 30 days.
 * Practice hands off to Learn/Test at /workspace/[documentId].
 * Layout matches coding / system-design practice siblings (left header, dense cards).
 */
import { useEffect } from "react";
import { Box, Button, Container, Group, Paper, Stack, Text, } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useNewspaperCatalogQuery } from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";
import { LearnerPageHeader } from "@/app/_components/study/LearnerPageHeader";
import { NewspaperBrandMark } from "@/app/_components/newspaper/NewspaperBrandMark";
export default function NewspaperDoorPage() {
    const router = useRouter();
    const catalog = useNewspaperCatalogQuery();
    useEffect(() => {
        void ensureGuestSession();
    }, []);
    const retention = catalog.data?.retention_days ?? 30;
    return (<Shell>
      <Container size="md" py={{ base: 32, md: 56 }}>
        <Stack gap="lg">
          <LearnerPageHeader align="left" compact eyebrow="Newspaper" title="Today's papers" subtitle={`Pick a paper, pick a day (last ${retention} days). Same Learn flow, questions only.`}/>

          {pick(Boolean(catalog.isLoading), () => (<Text c="dimmed">Loading papers…</Text>), () => pick(Boolean(catalog.isError), () => (<Text c="terracotta">Couldn&rsquo;t load newspapers right now.</Text>), () => pick(Boolean((catalog.data?.papers.length ?? 0) === 0), () => (<Paper radius="xl" p="xl" withBorder bg="gray.0" shadow="paper">
              <Stack gap="xs">
                <Text ff="var(--font-serif)" fw={500} fz="lg">
                  Nothing ready yet
                </Text>
                <Text c="dimmed" size="sm">
                  Editions appear here once today&rsquo;s papers are cooked. Check back soon.
                </Text>
              </Stack>
            </Paper>), () => (<Stack gap="xs">
              {catalog.data!.papers.map((p) => (<Paper key={p.slug} radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
                  <Group justify="space-between" align="center" wrap="wrap" gap="sm">
                    <Group gap="md" align="center" wrap="nowrap" style={{ minWidth: 0, flex: "1 1 180px" }}>
                      <NewspaperBrandMark slug={p.slug} title={p.title}/>
                      <Box style={{ minWidth: 0 }}>
                        <Text fw={600} ff="var(--font-serif)">
                          {p.title}
                        </Text>
                        <Text size="sm" c="dimmed" style={{ overflowWrap: "anywhere" }}>
                          {p.ready_days} day{choose(Boolean(p.ready_days === 1), "", "s")} ready
                          {choose(Boolean(p.latest_date), ` · latest ${p.latest_date}`, "")}
                        </Text>
                      </Box>
                    </Group>
                    <Button radius="xl" variant="light" color="lavender" fullWidth maw={{ base: "100%", xs: 180 }} rightSection={<IconArrowRight size={16}/>} onClick={() => router.push(`/practice/newspaper/${p.slug}`)}>
                      Open
                    </Button>
                  </Group>
                </Paper>))}
            </Stack>))))}
        </Stack>
      </Container>
    </Shell>);
}
