import {
  Box,
  Container,
  Group,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import type { Metadata } from "next";
import { LinkAnchor, LinkBox } from "@/app/learn/_components/AppLink";

export const metadata: Metadata = {
  title: "Learn | Question Better.",
  description:
    "Clear explainers and practice questions on systems, exams, and ideas that stick.",
};

type LearnPostListItem = {
  slug: string;
  title: string;
  lede: string;
  stream: string;
  author_name: string;
  published_at: string | null;
  format: string;
};

type LearnListResponse = {
  items: LearnPostListItem[];
  total: number;
};

function apiBase(): string {
  return process.env.API_PROXY_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8200";
}

async function fetchPosts(): Promise<LearnListResponse> {
  try {
    const res = await fetch(`${apiBase()}/api/learn/posts?limit=40`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return { items: [], total: 0 };
    return (await res.json()) as LearnListResponse;
  } catch {
    return { items: [], total: 0 };
  }
}

export default async function LearnIndexPage() {
  const data = await fetchPosts();
  const items = data.items ?? [];

  return (
    <Box bg="var(--mantine-color-body)" mih="100dvh">
      <Container size="md" py="xl" px="md">
        <Stack gap="xl">
          <Stack gap="xs">
            <LinkAnchor href="/" c="lavender.7" size="sm" underline="never">
              Question Better.
            </LinkAnchor>
            <Title
              order={1}
              ff="var(--font-serif)"
              fw={500}
              style={{ fontSize: "clamp(2rem, 5vw, 2.75rem)", lineHeight: 1.2 }}
            >
              Learn
            </Title>
            <Text c="dimmed" maw={520}>
              Short, clear articles with practice questions. Soft path into Question Better when you want to go deeper.
            </Text>
          </Stack>

          {items.length === 0 ? (
            <Text c="dimmed">No articles yet. Check back soon.</Text>
          ) : (
            <Stack gap="md">
              {items.map((p) => (
                <LinkBox
                  key={p.slug}
                  href={`/learn/${p.slug}`}
                  style={{ textDecoration: "none", color: "inherit" }}
                >
                  <Stack
                    gap={6}
                    p="md"
                    bg="gray.0"
                    style={{
                      borderRadius: "var(--mantine-radius-xl)",
                      border: "1px solid var(--mantine-color-default-border)",
                    }}
                  >
                    <Group gap="xs">
                      <Text size="xs" c="dimmed" tt="uppercase">
                        {p.stream === "system_design" ? "System design" : "General"}
                      </Text>
                      <Text size="xs" c="dimmed">
                        ·
                      </Text>
                      <Text size="xs" c="dimmed">
                        {p.format}
                      </Text>
                    </Group>
                    <Title order={3} ff="var(--font-serif)" fw={500} size="h3">
                      {p.title}
                    </Title>
                    {p.lede ? (
                      <Text size="sm" c="dimmed" lineClamp={2}>
                        {p.lede}
                      </Text>
                    ) : null}
                    <Text size="xs" c="dimmed">
                      {p.author_name}
                      {p.published_at
                        ? ` · ${new Date(p.published_at).toLocaleDateString("en-IN", {
                            year: "numeric",
                            month: "short",
                            day: "numeric",
                          })}`
                        : ""}
                    </Text>
                  </Stack>
                </LinkBox>
              ))}
            </Stack>
          )}
        </Stack>
      </Container>
    </Box>
  );
}
