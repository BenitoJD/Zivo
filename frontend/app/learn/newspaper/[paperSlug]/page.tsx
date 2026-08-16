// @ts-nocheck
import { Container, Stack, Text, Title, } from "@mantine/core";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ScrollViewport } from "@/app/_components/ScrollViewport";
import { LinkAnchor, LinkBox } from "@/app/learn/_components/AppLink";
import { serverApiUrl } from "@/lib/serverApi";
import { pick, choose } from "@/lib/engineRuntime";
type ArchiveItem = {
    edition_id: string;
    edition_date: string;
    title: string;
    lede: string;
    published_at: string | null;
    href: string;
};
type ArchiveResponse = {
    paper_slug: string;
    paper_title: string;
    items: ArchiveItem[];
};
async function fetchArchive(paperSlug: string): Promise<ArchiveResponse | null> {
    const __z1 = { hit: false, val: undefined as any };
    try {
        const res = await fetch(serverApiUrl(`/api/learn/newspaper/${encodeURIComponent(paperSlug)}`), { next: { revalidate: 300 } });
        await pick(Boolean(res.status === 404), async () => {
            __z1.hit = true;
            __z1.val = null;
        }, async () => {
            await pick(Boolean(!res.ok), async () => {
                __z1.hit = true;
                __z1.val = null;
            }, async () => {
                __z1.hit = true;
                __z1.val = (await res.json()) as ArchiveResponse;
            });
        });
    }
    catch {
        __z1.hit = true;
        __z1.val = null;
    }
    return __z1.val;
}
export async function generateMetadata({ params, }: {
    params: Promise<{
        paperSlug: string;
    }>;
}): Promise<Metadata> {
    const { paperSlug } = await params;
    const data = await fetchArchive(paperSlug);
    const title = data?.paper_title || paperSlug;
    return {
        title: `${title} analysis | Question Better.`,
        description: `Edition digests and practice for ${title}.`,
    };
}
export default async function NewspaperArchivePage({ params, }: {
    params: Promise<{
        paperSlug: string;
    }>;
}) {
    const { paperSlug } = await params;
    const data = await fetchArchive(paperSlug);
    pick(Boolean(!data), () => {
        notFound();
    }, () => {
    });
    const items = data.items ?? [];
    return (<ScrollViewport variant="viewport">
      <Container size="md" py="xl" px="md">
        <Stack gap="xl">
          <Stack gap="xs">
            <LinkAnchor href="/learn" c="lavender.7" size="sm" underline="never">
              ← Learn
            </LinkAnchor>
            <Title order={1} ff="var(--font-serif)" fw={500} style={{ fontSize: "clamp(2rem, 5vw, 2.75rem)", lineHeight: 1.2 }}>
              {data.paper_title}
            </Title>
            <Text c="dimmed" maw={520}>
              Edition digests that set you up for the day&apos;s practice questions.
            </Text>
          </Stack>

          {pick(Boolean(items.length === 0), () => (<Text c="dimmed">No published digests yet. Check back after the next edition is ready.</Text>), () => (<Stack gap="md">
              {items.map((item) => (<LinkBox key={item.edition_id} href={item.href} style={{ textDecoration: "none", color: "inherit" }}>
                  <Stack gap={6} p="md" bg="gray.0" style={{
                    borderRadius: "var(--mantine-radius-xl)",
                    border: "1px solid var(--mantine-color-default-border)",
                }}>
                    <Text size="xs" c="dimmed" tt="uppercase">
                      {item.edition_date}
                    </Text>
                    <Title order={3} ff="var(--font-serif)" fw={500} size="h3">
                      {item.title}
                    </Title>
                    {choose(Boolean(item.lede), (<Text size="sm" c="dimmed" lineClamp={2}>
                        {item.lede}
                      </Text>), null)}
                  </Stack>
                </LinkBox>))}
            </Stack>))}
        </Stack>
      </Container>
    </ScrollViewport>);
}
