// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
/**
 * Newspaper paper — pick a day (last 30 days), then hand off to Learn workspace.
 * Explicit secondary CTA opens Test (docs/QUESTION_BUDGET_ENGINE.md §0).
 */
import { useEffect, use, useState } from "react";
import { Badge, Button, Container, Group, Paper, Stack, Text, } from "@mantine/core";
import { IconArrowRight, IconBook2, IconClipboardList } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useNewspaperCatalogQuery, useNewspaperDaysQuery } from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";
import { LearnerPageHeader } from "@/app/_components/study/LearnerPageHeader";
import { NewspaperBrandMark } from "@/app/_components/newspaper/NewspaperBrandMark";
/** Human-readable title for a paper slug, used as a fallback while the days
 *  query is still loading so the header never shows a raw slug like "the-hindu". */
function humanizeSlug(slug: string): string {
    return slug
        .split("-")
        .map((w) => (pick(Boolean(w.length > 0), () => w[0].toUpperCase() + w.slice(1), () => w)))
        .join(" ");
}
export default function NewspaperPaperPage({ params, }: {
    params: Promise<{
        slug: string;
    }>;
}) {
    const { slug } = use(params);
    const router = useRouter();
    const [guestReady, setGuestReady] = useState(false);
    const daysQ = useNewspaperDaysQuery(slug, guestReady);
    // The catalog is tiny and cached; it carries the proper paper title so the
    // header renders "The Hindu" instead of the raw slug while days are loading.
    const catalogQ = useNewspaperCatalogQuery();
    useEffect(() => {
        void ensureGuestSession().then(() => setGuestReady(true));
    }, []);
    const title = daysQ.data?.paper_title ??
        catalogQ.data?.papers.find((p) => p.slug === slug)?.title ??
        humanizeSlug(slug);
    return (<Shell>
      <Container size="md" py={{ base: 32, md: 56 }}>
        <Stack gap="lg">
          <Group gap="md" align="center" wrap="wrap">
            <NewspaperBrandMark slug={slug} title={title} size={56}/>
            <LearnerPageHeader align="left" compact eyebrow="Newspaper" title={title} subtitle="Last 30 days. Open a day to Learn. Test is optional."/>
          </Group>

          {pick(Boolean(daysQ.isLoading), () => (<Text c="dimmed">Loading days…</Text>), () => pick(Boolean(daysQ.isError), () => (<Text c="terracotta">Couldn&rsquo;t load days.</Text>), () => pick(Boolean((daysQ.data?.days.length ?? 0) === 0), () => (<Text c="dimmed">No editions in the last 30 days yet.</Text>), () => (<Stack gap="xs">
              {daysQ.data!.days.map((d) => {
                const ready = d.status === "ready";
                const canOpen = pick(Boolean(d.document_id), () => ready, () => Boolean(d.document_id));
                return (<Paper key={d.id} radius="xl" p={{ base: "md", sm: "lg" }} withBorder bg="gray.0" shadow="paper">
                    <Stack gap={10}>
                      <Group justify="space-between" align="center" gap="md" wrap="nowrap">
                        <Text fw={600} ff="var(--font-serif)" fz={{ base: "md", sm: "lg" }} style={{ minWidth: 0 }} truncate="end">
                          {d.edition_date}
                        </Text>
                        <Group gap={6} wrap="wrap" justify="flex-end" style={{ flexShrink: 0 }}>
                          <Badge variant="light" color={choose(Boolean(ready), "sage", choose(Boolean(d.status === "failed"), "terracotta", "lavender"))} radius="xl">
                            {choose(Boolean(ready), "Ready", choose(Boolean(d.status === "indexing" || d.status === "pending"), "Preparing", d.status))}
                          </Badge>
                          {choose(Boolean(d.learner?.learn_complete), (<Badge variant="light" color="sage" radius="xl">
                              Complete
                            </Badge>), choose(Boolean(d.learner?.in_progress), (<Badge variant="light" color="lavender" radius="xl">
                              {choose(Boolean(d.learner.questions_total > 0), `${d.learner.questions_answered}/${d.learner.questions_total}`, `${d.learner.questions_answered} answered`)}
                            </Badge>), null))}
                        </Group>
                      </Group>
                      <Group gap="xs" wrap="wrap" justify="space-between" align="center" pt={2} style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
                        <Group gap="xs" wrap="wrap">
                          {/*..............................................................................*/choose(Boolean(d.has_blog && d.blog_href), (<Button radius="xl" size="compact-sm" variant="subtle" color="lavender" leftSection={<IconBook2 size={15}/>} onClick={() => router.push(d.blog_href!)}>
                              Read analysis
                            </Button>), null)}
                          <Button radius="xl" size="compact-sm" variant="subtle" color="gray" disabled={!canOpen} leftSection={<IconClipboardList size={15}/>} onClick={() => {
                        return pick(Boolean(!d.document_id), () => {
                            return;
                        }, () => {
                            router.push(`/workspace/${d.document_id}?mode=test`);
                        });
                    }}>
                            Test this edition
                          </Button>
                        </Group>
                        <Button radius="xl" size="sm" variant="light" color="lavender" disabled={!canOpen} rightSection={<IconArrowRight size={16}/>} onClick={() => {
                        return pick(Boolean(!d.document_id), () => {
                            return;
                        }, () => {
                            router.push(`/practice/newspaper/e/${d.id}`);
                        });
                    }}>
                          {choose(Boolean(d.learner?.in_progress), "Continue", "Learn")}
                        </Button>
                      </Group>
                    </Stack>
                  </Paper>);
            })}
            </Stack>))))}
        </Stack>
      </Container>
    </Shell>);
}
