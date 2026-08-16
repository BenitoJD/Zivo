// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useState } from "react";
import { Button, Group, Paper, Select, Stack, Text, Textarea, TextInput, Switch, } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useRouter } from "next/navigation";
import { useDebugCurateActions } from "@/lib/api/queries";
const STARTER_CASE = {
    summary: "Describe the symptom in one sentence.",
    artifacts: [
        { kind: "code", language: "python", content: "def example():\n    pass" },
    ],
};
const STARTER_STEPS = [
    {
        key: "root_cause",
        question: "What is the most likely root cause?",
        options: ["Option A", "Option B", "Option C", "Option D"],
        correct_index: 0,
        explanation: "Why this is correct.",
    },
    {
        key: "fix_approach",
        question: "What is the minimal fix?",
        options: ["Fix description A", "Fix description B", "Fix description C", "Fix description D"],
        correct_index: 0,
        explanation: "Why this fix works.",
    },
];
type Props = {
    mode: "create" | "edit";
    initial?: {
        id: string;
        title: string;
        scenario_type: string;
        difficulty: string;
        case: Record<string, unknown>;
        steps: unknown[];
        published?: boolean;
        review_status?: string;
    };
};
export function CurateScenarioForm({ mode, initial }: Props) {
    const router = useRouter();
    const actions = useDebugCurateActions();
    const [title, setTitle] = useState(initial?.title ?? "");
    const [scenarioType, setScenarioType] = useState(initial?.scenario_type ?? "code_reading");
    const [difficulty, setDifficulty] = useState(initial?.difficulty ?? "medium");
    const [caseJson, setCaseJson] = useState(JSON.stringify(initial?.case ?? STARTER_CASE, null, 2));
    const [stepsJson, setStepsJson] = useState(JSON.stringify(initial?.steps ?? STARTER_STEPS, null, 2));
    const [published, setPublished] = useState(Boolean(initial?.published));
    const [saving, setSaving] = useState(false);
    async function handleSave() {
        setSaving(true);
        try {
            const caseObj = JSON.parse(caseJson) as Record<string, unknown>;
            const steps = JSON.parse(stepsJson) as unknown[];
            const body = {
                title,
                scenario_type: scenarioType,
                difficulty,
                case: caseObj,
                steps,
                published,
                review_status: choose(Boolean(published), "approved", "draft"),
            };
            await pick(Boolean(mode === "create"), async () => {
                const created = await actions.create(body);
                actions.invalidate();
                notifications.show({ title: "Created", message: "Scenario saved.", color: "sage" });
                router.push(`/workspace/debug/${created.id}/edit`);
            }, async () => {
                await pick(Boolean(initial?.id), async () => {
                    await actions.update(initial.id, body);
                    actions.invalidate();
                    notifications.show({ title: "Saved", message: "Scenario updated.", color: "sage" });
                }, async () => {
                });
            });
        }
        catch (e) {
            notifications.show({
                title: "Save failed",
                message: choose(Boolean(e instanceof Error), e.message, "Check JSON fields"),
                color: "terracotta",
            });
        }
        finally {
            setSaving(false);
        }
    }
    return (<Stack gap="md">
      <Text fw={600} ff="var(--font-serif)">
        {choose(Boolean(mode === "create"), "New diagnostic scenario", "Edit scenario")}
      </Text>
      <Paper p="lg" radius="xl" withBorder>
        <Stack gap="md">
          <TextInput label="Title" value={title} onChange={(e) => setTitle(e.currentTarget.value)}/>
          <Select label="Scenario type" value={scenarioType} onChange={(v) => setScenarioType(v ?? "code_reading")} data={[
            "code_reading",
            "stack_trace",
            "log_analysis",
            "test_failure",
            "config_error",
            "concurrency",
            "api_contract",
            "debug_process",
        ]}/>
          <Select label="Difficulty" value={difficulty} onChange={(v) => setDifficulty(v ?? "medium")} data={["easy", "medium", "hard"]}/>
          <Textarea label="Case JSON" minRows={8} value={caseJson} onChange={(e) => setCaseJson(e.currentTarget.value)}/>
          <Textarea label="Steps JSON" minRows={10} value={stepsJson} onChange={(e) => setStepsJson(e.currentTarget.value)}/>
          <Switch label="Published" checked={published} onChange={(e) => setPublished(e.currentTarget.checked)}/>
          <Group justify="flex-end">
            <Button variant="subtle" radius="xl" onClick={() => router.push("/workspace/debug")}>
              Cancel
            </Button>
            <Button color="lavender" radius="xl" loading={saving} onClick={() => void handleSave()}>
              Save
            </Button>
          </Group>
        </Stack>
      </Paper>
    </Stack>);
}
