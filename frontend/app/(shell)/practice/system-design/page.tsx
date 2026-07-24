"use client";

/**
 * System Design mastery door — path whisper, recommended case, resume.
 */

import { useEffect } from "react";
import {
  Badge,
  Box,
  Button,
  Container,
  Drawer,
  Group,
  Paper,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconArrowRight,
  IconBuildingSkyscraper,
  IconPlayerPlay,
  IconRoute,
} from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import {
  useSystemDesignPathQuery,
  useSystemDesignRecommendedQuery,
  type SdConceptState,
  type SdPathConcept,
} from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";

function stateLabel(state: SdConceptState): string {
  if (state === "strong") return "strong";
  if (state === "needs_work") return "needs work";
  if (state === "in_progress") return "in progress";
  return "not started";
}

function stateColor(state: SdConceptState): string {
  if (state === "strong") return "sage";
  if (state === "needs_work") return "terracotta";
  if (state === "in_progress") return "lavender";
  return "gray";
}

export default function SystemDesignDoorPage() {
  const router = useRouter();
  const [pathOpen, { open: openPath, close: closePath }] = useDisclosure(false);
  const pathQ = useSystemDesignPathQuery();
  const recQ = useSystemDesignRecommendedQuery();

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  const focusTitle = recQ.data?.focus_title || pathQ.data?.focus_title || "";
  const focusKey = recQ.data?.focus_key || pathQ.data?.focus_key;
  const focusConcept = pathQ.data?.concepts.find((c) => c.key === focusKey);
  const problem = recQ.data?.problem;
  const active = recQ.data?.active_session;
  const loading = pathQ.isLoading || recQ.isLoading;
  const errored = pathQ.isError || recQ.isError;

  const begin = () => {
    if (active?.problem_id) {
      router.push(`/practice/system-design/${active.problem_id}`);
      return;
    }
    if (problem?.id) router.push(`/practice/system-design/${problem.id}`);
  };

  return (
    <Shell>
      <Container size="sm" py={{ base: 36, md: 64 }}>
        <Stack gap="xl">
          <Group gap="sm" align="center">
            <ThemeIcon variant="light" color="lavender" size={44} radius="xl">
              <IconBuildingSkyscraper size={22} />
            </ThemeIcon>
            <Box>
              <Title order={2} ff="var(--font-serif)" fw={500}>
                System Design
              </Title>
              <Text c="dimmed" fz="sm">
                Path → case → design → mentor truth → close the gap.
              </Text>
            </Box>
          </Group>

          {loading ? (
            <Text c="dimmed">Loading your path…</Text>
          ) : errored ? (
            <Text c="terracotta">Couldn&rsquo;t load System Design right now.</Text>
          ) : (
            <>
              {focusTitle ? (
                <Text fz="sm" c="dimmed">
                  <Text span ff="var(--font-serif)" c="gray.8" fw={500}>
                    {focusTitle.split("·")[0]?.trim() || focusTitle}
                  </Text>
                  {" · "}
                  {stateLabel(focusConcept?.state ?? "not_started")}
                </Text>
              ) : null}

              <Paper radius="xl" p={{ base: "lg", md: "xl" }} withBorder bg="gray.0" shadow="paper">
                <Stack gap="md">
                  <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
                    <Box maw={420}>
                      <Text size="xs" fw={600} tt="uppercase" lts={1.2} c="lavender.8" mb={6}>
                        {active ? "Resume" : "Recommended"}
                      </Text>
                      <Title order={3} ff="var(--font-serif)" fw={500} lh={1.25}>
                        {active?.problem?.title || problem?.title || "No case ready"}
                      </Title>
                      <Text c="dimmed" fz="sm" mt="xs" lineClamp={3}>
                        {active?.problem?.prompt || problem?.prompt || "Seed the bank, then return."}
                      </Text>
                    </Box>
                    {(active?.problem || problem) && (
                      <Badge
                        variant="light"
                        color={
                          (active?.problem?.difficulty || problem?.difficulty) === "easy"
                            ? "sage"
                            : (active?.problem?.difficulty || problem?.difficulty) === "hard"
                              ? "terracotta"
                              : "lavender"
                        }
                        radius="sm"
                        tt="capitalize"
                      >
                        {active?.problem?.difficulty || problem?.difficulty}
                      </Badge>
                    )}
                  </Group>

                  <Group gap="sm" wrap="wrap">
                    <Button
                      radius="xl"
                      color="lavender"
                      rightSection={
                        active ? <IconPlayerPlay size={16} /> : <IconArrowRight size={16} />
                      }
                      onClick={begin}
                      disabled={!active && !problem}
                    >
                      {active ? "Resume design" : "Begin"}
                    </Button>
                    <Button
                      radius="xl"
                      variant="subtle"
                      color="gray"
                      leftSection={<IconRoute size={16} />}
                      onClick={openPath}
                    >
                      See path
                    </Button>
                  </Group>
                </Stack>
              </Paper>
            </>
          )}
        </Stack>
      </Container>

      <Drawer
        opened={pathOpen}
        onClose={closePath}
        position="right"
        size="sm"
        title={
          <Text ff="var(--font-serif)" fw={500} fz="lg">
            Mastery path
          </Text>
        }
        overlayProps={{ backgroundOpacity: 0.45, blur: 8 }}
        padding="md"
      >
        <Stack gap="sm">
          {(pathQ.data?.concepts ?? []).map((c) => (
            <PathRow key={c.key} concept={c} focused={c.key === focusKey} />
          ))}
          {(pathQ.data?.concepts.length ?? 0) === 0 ? (
            <Text c="dimmed" fz="sm">
              No concepts yet — seed the bank.
            </Text>
          ) : null}
        </Stack>
      </Drawer>
    </Shell>
  );
}

function PathRow({ concept, focused }: { concept: SdPathConcept; focused: boolean }) {
  return (
    <Paper
      radius="lg"
      p="md"
      withBorder
      bg={focused ? "lavender.0" : "gray.0"}
      style={{
        borderColor: focused ? "var(--mantine-color-lavender-3)" : undefined,
      }}
    >
      <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
        <Box style={{ minWidth: 0 }}>
          <Text fw={600} fz="sm" ff="var(--font-serif)">
            {concept.title}
          </Text>
          <Text fz="xs" c="dimmed" lineClamp={2} mt={2}>
            {concept.blurb}
          </Text>
        </Box>
        <Badge variant="light" color={stateColor(concept.state)} radius="sm" tt="none">
          {stateLabel(concept.state)}
        </Badge>
      </Group>
    </Paper>
  );
}
