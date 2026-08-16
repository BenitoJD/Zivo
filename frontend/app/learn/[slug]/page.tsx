// @ts-nocheck
import { Box, Container, Group, Stack, Text, Title, } from "@mantine/core";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AssistantMarkdown } from "@/lib/chatMarkdown";
import { ScrollViewport } from "@/app/_components/ScrollViewport";
import { LearnMcqSection } from "@/app/learn/_components/LearnMcqSection";
import { LinkAnchor, LinkButton } from "@/app/learn/_components/AppLink";
import { MCQ_CONTENT_MAX } from "@/app/_components/mcq/McqCard";
import { serverApiUrl } from "@/lib/serverApi";
import { pick, choose } from "@/lib/engineRuntime";
type LearnPost = {
    slug: string;
    title: string;
    lede: string;
    body_md: string;
    format: string;
    stream: string;
    author_name: string;
    published_at: string | null;
    faq_jsonld?: {
        question: string;
        answer: string;
    }[];
    cta_kind?: string;
};
async function fetchPost(slug: string): Promise<LearnPost | null> {
    const __z1 = { hit: false, val: undefined as any };
    try {
        const res = await fetch(serverApiUrl(`/api/learn/posts/${encodeURIComponent(slug)}`), {
            next: { revalidate: 300 },
        });
        await pick(Boolean(res.status === 404), async () => {
            __z1.hit = true;
            __z1.val = null;
        }, async () => {
            await pick(Boolean(!res.ok), async () => {
                __z1.hit = true;
                __z1.val = null;
            }, async () => {
                __z1.hit = true;
                __z1.val = (await res.json()) as LearnPost;
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
        slug: string;
    }>;
}): Promise<Metadata> {
    const { slug } = await params;
    const post = await fetchPost(slug);
    return pick(Boolean(!post), () => ({ title: "Not found | Question Better." }), () => ({
        title: `${post.title} | Question Better.`,
        description: post.lede || post.title,
        openGraph: {
            title: post.title,
            description: post.lede || post.title,
            type: "article",
            authors: choose(Boolean(post.author_name), [post.author_name], undefined),
        },
    }));
}
function ctaHref(kind: string | undefined, stream: string): string {
    return pick(Boolean(kind === "system_design" || stream === "system_design"), () => "/practice/system-design", () => pick(Boolean(kind === "signup"), () => "/signup", () => "/practice"));
}
function ctaLabel(kind: string | undefined, stream: string): string {
    return pick(Boolean(kind === "system_design" || stream === "system_design"), () => "Try System Design practice", () => pick(Boolean(kind === "signup"), () => "Create a free account", () => "Practice on Question Better"));
}
function jsonLdScript(data: unknown): string {
    // Prevent </script> breakout from admin/user strings inside JSON-LD.
    return JSON.stringify(data).replace(/</g, "\\u003c");
}
export default async function LearnPostPage({ params, }: {
    params: Promise<{
        slug: string;
    }>;
}) {
    const { slug } = await params;
    const post = await fetchPost(slug);
    pick(Boolean(!post), () => {
        notFound();
    }, () => {
    });
    const faq = choose(Boolean(Array.isArray(post.faq_jsonld)), post.faq_jsonld, []);
    const articleLd = {
        "@context": "https://schema.org",
        "@type": "Article",
        headline: post.title,
        description: post.lede,
        author: { "@type": "Person", name: post.author_name },
        datePublished: post.published_at ?? undefined,
    };
    const faqLd = pick(Boolean(post.format === "faq" && faq.length > 0), () => ({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        mainEntity: faq.map((item) => ({
            "@type": "Question",
            name: item.question,
            acceptedAnswer: { "@type": "Answer", text: item.answer },
        })),
    }), () => null);
    return (<ScrollViewport variant="viewport">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdScript(articleLd) }}/>
      {pick(Boolean(faqLd), () => (<script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdScript(faqLd) }}/>), () => null)}
      <Container size={MCQ_CONTENT_MAX} py="xl" px="md">
        <Stack gap="lg">
          <Stack gap="xs">
            <LinkAnchor href="/learn" c="lavender.7" size="sm" underline="never">
              ← Learn
            </LinkAnchor>
            <Text size="xs" c="dimmed" tt="uppercase">
              {choose(Boolean(post.stream === "system_design"), "System design", "General")}
              {pick(Boolean(post.published_at), () => ` · ${new Date(post.published_at).toLocaleDateString("en-IN", {
            year: "numeric",
            month: "short",
            day: "numeric",
        })}`, () => "")}
            </Text>
            <Title order={1} ff="var(--font-serif)" fw={500} style={{ fontSize: "clamp(1.75rem, 4vw, 2.4rem)", lineHeight: 1.25 }}>
              {post.title}
            </Title>
            {choose(Boolean(post.lede), (<Text size="lg" c="dimmed" ff="var(--font-serif)" fs="italic">
                {post.lede}
              </Text>), null)}
            <Text size="sm" c="dimmed">
              By {post.author_name}
            </Text>
          </Stack>

          <Box style={{
            fontFamily: "var(--font-serif), Georgia, serif",
            fontSize: "1.1rem",
            lineHeight: 1.7,
        }}>
            <AssistantMarkdown content={post.body_md} isDark={false}/>
          </Box>

          <LearnMcqSection slug={slug}/>

          <Box mt="md" p="lg" bg="lavender.0" style={{
            borderRadius: "var(--mantine-radius-xl)",
            border: "1px solid var(--mantine-color-lavender-2)",
        }}>
            <Stack gap="sm">
              <Text fw={600} ff="var(--font-serif)">
                Go further on Question Better
              </Text>
              <Text size="sm" c="dimmed">
                Practice from your own sources, or walk a System Design path with teach-gap lessons.
              </Text>
              <Group>
                <LinkButton href={ctaHref(post.cta_kind, post.stream)} radius="xl" color="lavender">
                  {ctaLabel(post.cta_kind, post.stream)}
                </LinkButton>
                <LinkButton href="/signup" radius="xl" variant="default">
                  Sign up free
                </LinkButton>
              </Group>
            </Stack>
          </Box>
        </Stack>
      </Container>
    </ScrollViewport>);
}
