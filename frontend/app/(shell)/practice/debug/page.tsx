"use client";

import { pick, choose } from "@/lib/engineRuntime";
/**
 * Debug diagnostics door — browse published scenarios.
 * Layout matches newspaper / coding practice siblings (left header, dense cards).
 */
import { useEffect } from "react";
import { Anchor, Box, Button, Container, Group, Paper, Stack, Text, } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useDebugPublicQuery, useSessionQuery } from "@/lib/api/queries";
import { Shell } from "@/app/practice/_components/Shell";
import { LearnerPageHeader } from "@/app/_components/study/LearnerPageHeader";
import { DebugScenarioMark } from "@/app/_components/debug/DebugScenarioMark";
function scenarioMeta(item: {
    step_count: number;
    difficulty: string;
    scenario_type: string;
}) {
    const steps = `${item.step_count} step${choose(Boolean(item.step_count === 1), "", "s")}`;
    const kind = item.scenario_type.replace(/_/g, " ");
    return `${steps} · ${item.difficulty} · ${kind}`;
}
export default function DebugPracticePage() {
    const router = useRouter();
    const session = useSessionQuery();
    const isAdmin = Boolean(session.data?.is_admin);
    const { data, isLoading, isError } = useDebugPublicQuery({});
    useEffect(() => {
        void ensureGuestSession();
    }, []);
    const items = data?.items ?? [];
    return (<Shell>
      <Container size="md" py={{ base: 32, md: 56 }}>
        <Stack gap="lg">
          <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
            <LearnerPageHeader align="left" compact eyebrow="Debug diagnostics" title="Find what's wrong" subtitle="Read a failure case, diagnose the root cause, pick the fix. Same stepped flow, no coding required."/>
            {choose(Boolean(isAdmin), (<Anchor href="/workspace/debug" fz="sm" c="lavender.7">
                Curate bank →
              </Anchor>), null)}
          </Group>

          {pick(Boolean(isLoading), () => (<Text c="dimmed">Loading scenarios…</Text>), () => pick(Boolean(isError), () => (<Text c="terracotta">Couldn&rsquo;t load scenarios right now.</Text>), () => pick(Boolean(items.length === 0), () => (<Paper radius="xl" p="xl" withBorder bg="gray.0" shadow="paper">
              <Stack gap="xs">
                <Text ff="var(--font-serif)" fw={500} fz="lg">
                  Nothing ready yet
                </Text>
                <Text c="dimmed" size="sm">
                  Scenarios appear here once they are cooked and published. Check back soon.
                </Text>
              </Stack>
            </Paper>), () => (<Stack gap="xs">
              {items.map((item) => (<Paper key={item.id} radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
                  <Group justify="space-between" align="center" wrap="wrap" gap="sm">
                    <Group gap="md" align="center" wrap="nowrap" style={{ minWidth: 0, flex: "1 1 180px" }}>
                      <DebugScenarioMark scenarioType={item.scenario_type}/>
                      <Box style={{ minWidth: 0 }}>
                        <Text fw={600} ff="var(--font-serif)">
                          {item.title}
                        </Text>
                        <Text size="sm" c="dimmed" style={{ overflowWrap: "anywhere" }}>
                          {scenarioMeta(item)}
                        </Text>
                      </Box>
                    </Group>
                    <Button radius="xl" variant="light" color="lavender" fullWidth maw={{ base: "100%", xs: 180 }} rightSection={<IconArrowRight size={16}/>} onClick={() => router.push(`/practice/debug/${item.id}`)}>
                      Open
                    </Button>
                  </Group>
                </Paper>))}
            </Stack>))))}
        </Stack>
      </Container>
    </Shell>);
}
