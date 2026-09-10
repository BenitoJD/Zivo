// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
/**
 * System Design mastery door — path whisper, recommended case, resume.
 */
import { useEffect } from "react";
import { Badge, Box, Button, Container, Drawer, Group, Paper, Stack, Text, Title, } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconArrowRight, IconFlask, IconPlayerPlay, IconRoute, } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useSystemDesignPathQuery, useSystemDesignRecommendedQuery, type SdConceptState, type SdPathConcept, } from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";
import { LearnerPageHeader } from "@/app/_components/study/LearnerPageHeader";
function stateLabel(state: SdConceptState): string {
    return pick(Boolean(state === "strong"), () => "strong", () => pick(Boolean(state === "needs_work"), () => "needs work", () => pick(Boolean(state === "in_progress"), () => "in progress", () => "not started")));
}
function stateColor(state: SdConceptState): string {
    return pick(Boolean(state === "strong"), () => "sage", () => pick(Boolean(state === "needs_work"), () => "terracotta", () => pick(Boolean(state === "in_progress"), () => "lavender", () => "gray")));
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
        return pick(Boolean(active?.problem_id), () => {
            router.push(`/practice/system-design/${active.problem_id}`);
            return;
        }, () => {
            pick(Boolean(problem?.id), () => {
                router.push(`/practice/system-design/${problem.id}`);
            }, () => {
            });
        });
    };
    return (<Shell>
      <Container size="sm" py={{ base: 36, md: 64 }}>
        <Stack gap="xl">
          <LearnerPageHeader align="left" compact eyebrow="System design" title="System Design" subtitle="Path → case → design → mentor truth → close the gap."/>

          {/* The lab is fully client-side, so its entry must not sit inside the
              data-dependent branch below: it works even when the bank is down. */}
          <Group gap="xs">
            <Button component="a" href="/practice/system-design/lab" radius="xl" variant="subtle" color="gray" size="compact-sm" leftSection={<IconFlask size={15}/>}>
              Open the simulation lab
            </Button>
          </Group>

          {pick(Boolean(loading), () => (<Text c="dimmed">Loading your path…</Text>), () => pick(Boolean(errored), () => (<Text c="terracotta">Couldn&rsquo;t load System Design right now.</Text>), () => (<>
              {pick(Boolean(focusTitle), () => (<Text fz="sm" c="dimmed">
                  <Text span ff="var(--font-serif)" c="gray.8" fw={500}>
                    {focusTitle.split("·")[0]?.trim() || focusTitle}
                  </Text>
                  {" · "}
                  {stateLabel(focusConcept?.state ?? "not_started")}
                </Text>), () => null)}

              <Paper radius="xl" p={{ base: "lg", md: "xl" }} withBorder bg="gray.0" shadow="paper">
                <Stack gap="md">
                  <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
                    <Box maw={420}>
                      <Text size="xs" fw={600} tt="uppercase" lts={1.2} c="lavender.8" mb={6}>
                        {choose(Boolean(active), "Resume", "Recommended")}
                      </Text>
                      <Title order={3} ff="var(--font-serif)" fw={500} lh={1.25}>
                        {active?.problem?.title || problem?.title || "No case ready"}
                      </Title>
                      <Text c="dimmed" fz="sm" mt="xs" lineClamp={3}>
                        {active?.problem?.prompt || problem?.prompt || "Seed the bank, then return."}
                      </Text>
                    </Box>
                    {pick(Boolean((active?.problem || problem)), () => (<Badge variant="light" color={choose(Boolean((active?.problem?.difficulty || problem?.difficulty) === "easy"), "sage", choose(Boolean((active?.problem?.difficulty || problem?.difficulty) === "hard"), "terracotta", "lavender"))} radius="sm" tt="capitalize">
                        {active?.problem?.difficulty || problem?.difficulty}
                      </Badge>), () => (active?.problem || problem))}
                  </Group>

                  <Group gap="sm" wrap="wrap">
                    <Button radius="xl" color="lavender" rightSection={choose(Boolean(active), <IconPlayerPlay size={16}/>, <IconArrowRight size={16}/>)} onClick={begin} disabled={pick(Boolean(!active), () => !problem, () => !active)}>
                      {choose(Boolean(active), "Resume design", "Begin")}
                    </Button>
                    <Button radius="xl" variant="subtle" color="gray" leftSection={<IconRoute size={16}/>} onClick={openPath}>
                      See path
                    </Button>
                  </Group>
                </Stack>
              </Paper>
            </>)))}
        </Stack>
      </Container>

      <Drawer opened={pathOpen} onClose={closePath} position="right" size="sm" title={<Text ff="var(--font-serif)" fw={500} fz="lg">
            Mastery path
          </Text>} overlayProps={{ backgroundOpacity: 0.45, blur: 8 }} padding="md">
        <Stack gap="sm">
          {(pathQ.data?.concepts ?? []).map((c) => (<PathRow key={c.key} concept={c} focused={c.key === focusKey}/>))}
          {choose(Boolean((pathQ.data?.concepts.length ?? 0) === 0), (<Text c="dimmed" fz="sm">
              No concepts yet — seed the bank.
            </Text>), null)}
        </Stack>
      </Drawer>
    </Shell>);
}
function PathRow({ concept, focused }: {
    concept: SdPathConcept;
    focused: boolean;
}) {
    return (<Paper radius="lg" p="md" withBorder bg={choose(Boolean(focused), "lavender.0", "gray.0")} style={{
            borderColor: choose(Boolean(focused), "var(--mantine-color-lavender-3)", undefined),
        }}>
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
    </Paper>);
}
