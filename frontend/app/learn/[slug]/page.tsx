import {
  Anchor,
  Box,
  Button,
  Container,
  Group,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import Link from "next/link";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AssistantMarkdown } from "@/lib/chatMarkdown";
import { LearnMcqSection } from "@/app/learn/_components/LearnMcqSection";

type LearnPost = {
  slug: string;
  title: string;
  lede: string;
  body_md: string;
  format: string;
  stream: string;
  author_name: string;
  published_at: string | null;
  faq_jsonld?: { question: string; answer: string }[];
  cta_kind?: string;
};

function apiBase(): string {
  return process.env.API_PROXY_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8200";
}

async function fetchPost(slug: string): Promise<LearnPost | null> {
  try {
    const res = await fetch(`${apiBase()}/api/learn/posts/${encodeURIComponent(slug)}`, {
      next: { revalidate: 300 },
    });
    if (res.status === 404) return null;
    if (!res.ok) return null;
    return (await res.json()) as LearnPost;
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const post = await fetchPost(slug);
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

function ctaHref(kind: string | undefined, stream: string): string {
  if (kind === "system_design" || stream === "system_design") return "/practice/system-design";
  if (kind === "signup") return "/signup";
  return "/practice";
}

function ctaLabel(kind: string | undefined, stream: string): string {
  if (kind === "system_design" || stream === "system_design") return "Try System Design practice";
  if (kind === "signup") return "Create a free account";
  return "Practice on Question Better";
}

function jsonLdScript(data: unknown): string {
  // Prevent </script> breakout from admin/user strings inside JSON-LD.
  return JSON.stringify(data).replace(/</g, "\\u003c");
}

export default async function LearnPostPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const post = await fetchPost(slug);
  if (!post) notFound();

  const faq = Array.isArray(post.faq_jsonld) ? post.faq_jsonld : [];
  const articleLd = {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: post.title,
    description: post.lede,
    author: { "@type": "Person", name: post.author_name },
    datePublished: post.published_at ?? undefined,
  };
  const faqLd =
    post.format === "faq" && faq.length > 0
      ? {
          "@context": "https://schema.org",
          "@type": "FAQPage",
          mainEntity: faq.map((item) => ({
            "@type": "Question",
            name: item.question,
            acceptedAnswer: { "@type": "Answer", text: item.answer },
          })),
        }
      : null;

  return (
    <Box bg="var(--mantine-color-body)" mih="100dvh">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLdScript(articleLd) }}
      />
      {faqLd ? (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: jsonLdScript(faqLd) }}
        />
      ) : null}
      <Container size="sm" py="xl" px="md">
        <Stack gap="lg">
          <Stack gap="xs">
            <Anchor component={Link} href="/learn" c="lavender.7" size="sm" underline="never">
              ← Learn
            </Anchor>
            <Text size="xs" c="dimmed" tt="uppercase">
              {post.stream === "system_design" ? "System design" : "General"}
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

          <LearnMcqSection slug={slug} />

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
                Go further on Question Better
              </Text>
              <Text size="sm" c="dimmed">
                Practice from your own sources, or walk a System Design path with teach-gap lessons.
              </Text>
              <Group>
                <Button
                  component={Link}
                  href={ctaHref(post.cta_kind, post.stream)}
                  radius="xl"
                  color="lavender"
                >
                  {ctaLabel(post.cta_kind, post.stream)}
                </Button>
                <Button component={Link} href="/signup" radius="xl" variant="default">
                  Sign up free
                </Button>
              </Group>
            </Stack>
          </Box>
        </Stack>
      </Container>
    </Box>
  );
}
