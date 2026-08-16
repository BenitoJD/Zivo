// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { use, useCallback, useEffect } from "react";
import { Box, Button, Center, Stack, Text } from "@mantine/core";
import { IconArrowLeft } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { ensureGuestSession } from "@/lib/api/client";
import { useDebugActions, useDebugScenarioQuery } from "@/lib/api/queries";
import { DebugScenarioPlayer } from "@/app/_components/debug/DebugScenarioPlayer";
export default function DebugSolvePage({ params }: {
    params: Promise<{
        id: string;
    }>;
}) {
    const router = useRouter();
    const { id } = use(params);
    const { data: scenario, isLoading, isError } = useDebugScenarioQuery(id, Boolean(id));
    const actions = useDebugActions();
    useEffect(() => {
        void ensureGuestSession();
    }, []);
    const onGradeStep = useCallback((stepKey: string, choiceIndex: number, stepsCorrect: number, stepsTotal: number) => actions.gradeStep(id, {
        step_key: stepKey,
        choice_index: choiceIndex,
        ...(choose(Boolean(stepsTotal > 0), { steps_correct: stepsCorrect, steps_total: stepsTotal }, {})),
    }), [actions, id]);
    return pick(Boolean(isLoading), () => (<Center py="xl">
        <Text c="dimmed">Loading scenario…</Text>
      </Center>), () => pick(Boolean(isError || !scenario), () => (<Box p="md">
        <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={14}/>} onClick={() => router.push("/practice/debug")} mb="sm">
          Back
        </Button>
        <Text c="dimmed">Scenario not found.</Text>
      </Box>), () => (<Box py={{ base: "md", md: "xl" }} px={{ base: "xs", sm: "md" }}>
      <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={14}/>} onClick={() => router.push("/practice/debug")} mb="md">
        All scenarios
      </Button>
      <Stack gap="lg">
        <DebugScenarioPlayer scenario={scenario} onGradeStep={onGradeStep}/>
      </Stack>
    </Box>)));
}
