import {
  Box,
  Container,
  Group,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AssistantMarkdown } from "@/lib/chatMarkdown";
import { LearnMcqSection } from "@/app/learn/_components/LearnMcqSection";
import { LinkAnchor, LinkButton } from "@/app/learn/_components/AppLink";
import { MCQ_CONTENT_MAX } from "@/app/_components/mcq/McqCard";

type EditionBlog = {
  slug: string;
  title: string;
  lede: string;
  body_md: string;
  author_name: string;
  published_at: string | null;
  edition_id: string;
  paper_slug: string;
  paper_title: string;
  edition_date: string;
  practice_href: string;
};

function apiBase(): string {
  return process.env.API_PROXY_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8200";
}

async function fetchEditionBlog(
  paperSlug: string,
  editionDate: string,
): Promise<EditionBlog | null> {
  try {
    const res = await fetch(
      `${apiBase()}/api/learn/newspaper/${encodeURIComponent(paperSlug)}/${encodeURIComponent(editionDate)}`,
      { next: { revalidate: 300 } },
    );
    if (res.status === 404) return null;
    if (!res.ok) return null;
    return (await res.json()) as EditionBlog;
  } catch {
    return null;
  }
}

function jsonLdScript(data: unknown): string {
  return JSON.stringify(data).replace(/</g, "\\u003c");
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ paperSlug: string; date: string }>;
}): Promise<Metadata> {
  const { paperSlug, date } = await params;
  const post = await fetchEditionBlog(paperSlug, date);
  if (!post) {
    return { title: "Not found | Question Better." };
  }
  return {
    title: `${post.title} | Question Better.`,
    description: post.lede || post.title,
    openGraph: {
      title: post.title,
      description: post.lede || post.title,
      type: "article",
      authors: post.author_name ? [post.author_name] : undefined,
    },
  };
}

export default async function NewspaperEditionBlogPage({
  params,
}: {
  params: Promise<{ paperSlug: string; date: string }>;
}) {
  const { paperSlug, date } = await params;
  const post = await fetchEditionBlog(paperSlug, date);
  if (!post) notFound();

  const archiveHref = `/learn/newspaper/${paperSlug}`;
  const articleLd = {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: post.title,
    description: post.lede,
    author: { "@type": "Person", name: post.author_name },
    datePublished: post.published_at ?? undefined,
  };

  return (
    <Box bg="var(--mantine-color-body)" mih="100dvh">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLdScript(articleLd) }}
      />
      <Container size={MCQ_CONTENT_MAX} py="xl" px="md">
        <Stack gap="lg">
          <Stack gap="xs">
            <LinkAnchor href="/learn" c="lavender.7" size="sm" underline="never">
              ← Learn
            </LinkAnchor>
            <LinkAnchor href={archiveHref} c="lavender.7" size="sm" underline="never">
              {post.paper_title}
            </LinkAnchor>
            <Text size="xs" c="dimmed" tt="uppercase">
              Newspaper edition
              {post.published_at
                ? ` · ${new Date(post.published_at).toLocaleDateString("en-IN", {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                  })}`
                : ""}
            </Text>
            <Title
              order={1}
              ff="var(--font-serif)"
              fw={500}
              style={{ fontSize: "clamp(1.75rem, 4vw, 2.4rem)", lineHeight: 1.25 }}
            >
              {post.title}
            </Title>
            {post.lede ? (
              <Text size="lg" c="dimmed" ff="var(--font-serif)" fs="italic">
                {post.lede}
              </Text>
            ) : null}
            <Text size="sm" c="dimmed">
              By {post.author_name}
            </Text>
          </Stack>

          <Box
            style={{
              fontFamily: "var(--font-serif), Georgia, serif",
              fontSize: "1.1rem",
              lineHeight: 1.7,
            }}
          >
            <AssistantMarkdown content={post.body_md} isDark={false} />
          </Box>

          <LearnMcqSection slug={post.slug} />

          <Box
            mt="md"
            p="lg"
            bg="lavender.0"
            style={{
              borderRadius: "var(--mantine-radius-xl)",
              border: "1px solid var(--mantine-color-lavender-2)",
            }}
          >
            <Stack gap="sm">
              <Text fw={600} ff="var(--font-serif)">
                Practice this edition
              </Text>
              <Text size="sm" c="dimmed">
                Open the full Learn workspace for this day&apos;s MCQs, tutor, and spaced review.
              </Text>
              <Group>
                <LinkButton href={post.practice_href} radius="xl" color="lavender">
                  Open in Learn
                </LinkButton>
                <LinkButton href="/signup" radius="xl" variant="default">
                  Sign up free
                </LinkButton>
              </Group>
            </Stack>
          </Box>
        </Stack>
      </Container>
    </Box>
  );
}
