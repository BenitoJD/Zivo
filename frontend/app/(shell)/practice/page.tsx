"use client";

/**
 * Practice library hub - search-first entry into the Wikidata concept graph.
 *
 * Guest session bootstrapped for anonymous visitors. Lives under the shared
 * learner AppShell (Sidebar) via `app/(shell)/layout.tsx`.
 */

import { useEffect, useState } from "react";
import {
  Anchor,
  Box,
  Button,
  Center,
  Container,
  Group,
  Paper,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { IconArrowRight, IconSearch } from "@tabler/icons-react";
import { apiGet, ensureGuestSession } from "@/lib/api/client";
import { Shell } from "@/app/practice/_components/Shell";
import { LearnerPageHeader } from "@/app/_components/study/LearnerPageHeader";
import { NewspaperBrandMark } from "@/app/_components/newspaper/NewspaperBrandMark";
import { DebugScenarioMark } from "@/app/_components/debug/DebugScenarioMark";

type ConceptSummary = { qid: string; label: string; description: string };
type SearchResponse = { query: string; results: ConceptSummary[] };

// A few evergreen concepts for first-time gravity (SEO + discoverability).
const CURATED: ConceptSummary[] = [
  { qid: "Q638", label: "Music", description: "The art of organized sound." },
  { qid: "Q11982", label: "Photosynthesis", description: "How plants convert light into chemical energy." },
  { qid: "Q41299", label: "Quadratic equation", description: "Solving second-degree polynomial equations." },
  { qid: "Q149972", label: "Calculus", description: "The mathematics of continuous change." },
  { qid: "Q336", label: "Science", description: "Systematic study of the natural world." },
  { qid: "Q38433", label: "Newton's laws of motion", description: "The foundations of classical mechanics." },
];

export default function PracticeHubPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ConceptSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<SearchResponse>(`/api/practice/search?q=${encodeURIComponent(q)}`);
      setResults(data.results);
    } catch {
      setError("Search is temporarily unavailable. Please try again.");
      setResults([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Shell>
      <Container size="md" py={{ base: 48, md: 72 }} pb={{ base: 88, md: 72 }}>
        <Stack gap="xl">
          <LearnerPageHeader
            eyebrow="Practice library"
            title="Practice"
            titleAccent="anything"
            subtitle="Search a concept — or open Newspaper, Coding, or System Design. Same Learn chrome when you answer."
          />

          {/* Search */}
          <Paper shadow="paper" radius="xl" p={6} withBorder bg="gray.0">
            <form onSubmit={handleSearch}>
              <Stack gap={6} hiddenFrom="sm">
                <TextInput
                  variant="unstyled"
                  placeholder="Search a topic…"
                  value={query}
                  onChange={(e) => setQuery(e.currentTarget.value)}
                  size="md"
                  leftSection={<IconSearch size={18} />}
                  styles={{ input: { fontSize: "1.05rem" } }}
                />
                <Button type="submit" radius="xl" fullWidth loading={loading} disabled={!query.trim()}>
                  Search
                </Button>
              </Stack>
              <Group gap={0} wrap="nowrap" visibleFrom="sm">
                <TextInput
                  variant="unstyled"
                  placeholder="Search a topic, e.g. “quadratic equations”"
                  value={query}
                  onChange={(e) => setQuery(e.currentTarget.value)}
                  size="md"
                  leftSection={<IconSearch size={18} />}
                  styles={{ input: { fontSize: "1.05rem" } }}
                  style={{ flex: 1 }}
                />
                <Button type="submit" radius="xl" loading={loading} disabled={!query.trim()}>
                  Search
                </Button>
              </Group>
            </form>
          </Paper>

          {error && (
            <Text size="sm" c="terracotta.7" ta="center">
              {error}
            </Text>
          )}

          {/* Results */}
          {results !== null ? (
            results.length === 0 ? (
              <Center py={40}>
                <Stack align="center" gap="xs">
                  <Text size="lg" fw={500} c="gray.7">
                    No concepts found for “{query}”.
                  </Text>
                  <Text size="sm" c="dimmed">
                    Try a broader term, or pick one below to start.
                  </Text>
                </Stack>
              </Center>
            ) : (
              <Stack gap="sm">
                <Text size="sm" c="dimmed" fw={500}>
                  {results.length} concept{results.length === 1 ? "" : "s"} found
                </Text>
                {results.map((c) => (
                  <ConceptCard key={c.qid} concept={c} />
                ))}
              </Stack>
            )
          ) : (
            <>
              {/* Curated landing */}
              <Stack gap="xs">
                <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
                  <Group justify="space-between" align="center" wrap="wrap" gap="sm">
                    <Group
                      gap="md"
                      align="center"
                      wrap="nowrap"
                      style={{ minWidth: 0, flex: "1 1 180px" }}
                    >
                      <NewspaperBrandMark slug="the-hindu" title="The Hindu" size={48} />
                      <Box style={{ minWidth: 0 }}>
                        <Text fw={600} ff="var(--font-serif)">
                          Newspaper
                        </Text>
                        <Text size="sm" c="dimmed" style={{ overflowWrap: "anywhere" }}>
                          Daily papers as questions — pick a day, practice, ask the tutor.
                        </Text>
                      </Box>
                    </Group>
                    <Button
                      component="a"
                      href="/practice/newspaper"
                      radius="xl"
                      variant="light"
                      color="lavender"
                      fullWidth
                      maw={{ base: "100%", xs: 180 }}
                      rightSection={<IconArrowRight size={16} />}
                    >
                      Open papers
                    </Button>
                  </Group>
                </Paper>
                <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
                  <Group justify="space-between" align="center" wrap="wrap" gap="sm">
                    <Group
                      gap="md"
                      align="center"
                      wrap="nowrap"
                      style={{ minWidth: 0, flex: "1 1 180px" }}
                    >
                      <DebugScenarioMark scenarioType="code_reading" size={48} />
                      <Box style={{ minWidth: 0 }}>
                        <Text fw={600} ff="var(--font-serif)">
                          Debug diagnostics
                        </Text>
                        <Text size="sm" c="dimmed" style={{ overflowWrap: "anywhere" }}>
                          Find what is wrong: read failures, diagnose root cause, pick the fix approach.
                        </Text>
                      </Box>
                    </Group>
                    <Button
                      component="a"
                      href="/practice/debug"
                      radius="xl"
                      variant="light"
                      color="lavender"
                      fullWidth
                      maw={{ base: "100%", xs: 180 }}
                      rightSection={<IconArrowRight size={16} />}
                    >
                      Open bank
                    </Button>
                  </Group>
                </Paper>
                <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
                  <Group justify="space-between" align="center" wrap="wrap" gap="sm">
                    <Box style={{ minWidth: 0, flex: "1 1 180px" }}>
                      <Text fw={600} ff="var(--font-serif)">
                        Coding practice
                      </Text>
                      <Text size="sm" c="dimmed" style={{ overflowWrap: "anywhere" }}>
                        LeetCode-style problems — run tests, submit, track solved.
                      </Text>
                    </Box>
                    <Button
                      component="a"
                      href="/practice/coding"
                      radius="xl"
                      variant="light"
                      color="lavender"
                      fullWidth
                      maw={{ base: "100%", xs: 180 }}
                      rightSection={<IconArrowRight size={16} />}
                    >
                      Open bank
                    </Button>
                  </Group>
                </Paper>
                <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
                  <Group justify="space-between" align="center" wrap="wrap" gap="sm">
                    <Box style={{ minWidth: 0, flex: "1 1 180px" }}>
                      <Text fw={600} ff="var(--font-serif)">
                        System Design
                      </Text>
                      <Text size="sm" c="dimmed" style={{ overflowWrap: "anywhere" }}>
                        Mastery path — design a case, get mentor truth, close the gap.
                      </Text>
                    </Box>
                    <Button
                      component="a"
                      href="/practice/system-design"
                      radius="xl"
                      variant="light"
                      color="lavender"
                      fullWidth
                      maw={{ base: "100%", xs: 180 }}
                      rightSection={<IconArrowRight size={16} />}
                    >
                      Open path
                    </Button>
                  </Group>
                </Paper>
                <Text size="sm" c="dimmed" fw={500} mt="sm">
                  Or explore a topic to get started
                </Text>
                {CURATED.map((c) => (
                  <ConceptCard key={c.qid} concept={c} />
                ))}
              </Stack>
            </>
          )}
        </Stack>
      </Container>
    </Shell>
  );
}

function ConceptCard({ concept }: { concept: ConceptSummary }) {
  return (
    <Anchor href={`/practice/c/${concept.qid}`} underline="never" style={{ display: "block" }}>
      <Paper
        radius="xl"
        p="lg"
        shadow="paper"
        bg="gray.0"
        withBorder
        style={{ transition: "transform 160ms ease, border-color 160ms ease" }}
        onMouseEnter={(e) => {
          e.currentTarget.style.transform = "translateY(-1px)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = "translateY(0)";
        }}
      >
        <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
          <Stack gap={4} style={{ minWidth: 0, flex: 1 }}>
            <Title
              order={3}
              fw={500}
              style={{
                fontFamily: "var(--font-serif), Georgia, serif",
                fontSize: "1.15rem",
                overflowWrap: "anywhere",
              }}
            >
              {concept.label}
            </Title>
            <Text size="sm" c="dimmed" lh={1.5} style={{ overflowWrap: "anywhere" }}>
              {concept.description || "Practice questions for this concept."}
            </Text>
          </Stack>
          <Box c="lavender.7" style={{ flexShrink: 0, marginTop: 4 }}>
            <IconArrowRight size={20} />
          </Box>
        </Group>
      </Paper>
    </Anchor>
  );
}
