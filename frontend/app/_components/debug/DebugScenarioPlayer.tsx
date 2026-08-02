"use client";

import { useCallback, useState } from "react";
import {
  Box,
  Button,
  Paper,
  Stack,
  Text,
  Title,
  UnstyledButton,
  Group,
  Badge,
} from "@mantine/core";
import { IconBulb, IconCheck } from "@tabler/icons-react";
import type { DebugScenario, DebugGradeStepResult } from "@/lib/api/queries";
import { MCQ_CONTENT_MAX } from "@/app/_components/mcq/McqCard";
import { apiPostSSE } from "@/lib/api/client";

type Props = {
  scenario: DebugScenario;
  onGradeStep: (stepKey: string, choiceIndex: number, stepsCorrect: number, stepsTotal: number) => Promise<DebugGradeStepResult>;
};

type StepState = {
  selected: number | null;
  graded: DebugGradeStepResult | null;
};

export function DebugScenarioPlayer({ scenario, onGradeStep }: Props) {
  const steps = scenario.steps ?? [];
  const total = steps.length;
  const [stepIndex, setStepIndex] = useState(0);
  const [stepStates, setStepStates] = useState<StepState[]>(() =>
    steps.map(() => ({ selected: null, graded: null })),
  );
  const [reflecting, setReflecting] = useState(false);
  const [reflectText, setReflectText] = useState("");
  const [done, setDone] = useState(false);

  const current = steps[stepIndex];
  const currentState = stepStates[stepIndex] ?? { selected: null, graded: null };

  const handleSelect = useCallback((idx: number) => {
    if (currentState.graded) return;
    setStepStates((prev) => {
      const next = [...prev];
      next[stepIndex] = { ...next[stepIndex], selected: idx };
      return next;
    });
  }, [currentState.graded, stepIndex]);

  const handleSubmitStep = useCallback(async () => {
    if (!current || currentState.selected === null || currentState.graded) return;
    const result = await onGradeStep(current.key, currentState.selected, 0, 0);
    setStepStates((prev) => {
      const next = [...prev];
      next[stepIndex] = { selected: currentState.selected, graded: result };
      return next;
    });
  }, [current, currentState, onGradeStep, stepIndex]);

  const handleContinue = useCallback(async () => {
    if (stepIndex < total - 1) {
      setStepIndex((i) => i + 1);
      return;
    }
    const finalCorrect = stepStates.filter((s) => s.graded?.correct).length;
    if (current) {
      await onGradeStep(current.key, currentState.selected ?? 0, finalCorrect, total);
    }
    setReflecting(true);
    setReflectText("");
    try {
      await apiPostSSE(`/api/debug/${scenario.id}/reflect/stream`, {}, {
        onEvent: (event, data) => {
          if (event === "token") {
            setReflectText((t) => t + data);
          }
        },
      });
    } catch {
      setReflectText("Review the explanations above and note what you would check first next time.");
    } finally {
      setReflecting(false);
      setDone(true);
    }
  }, [current, currentState.selected, onGradeStep, scenario.id, stepIndex, stepStates, total]);

  const artifacts = scenario.case?.artifacts ?? [];

  return (
    <Stack gap="lg" maw={MCQ_CONTENT_MAX} mx="auto" w="100%">
      <Box>
        <Badge variant="light" color="lavender" size="sm" mb="xs">
          {scenario.scenario_type.replace(/_/g, " ")}
        </Badge>
        <Title order={3} ff="var(--font-serif)" fw={500}>
          {scenario.title}
        </Title>
        {scenario.case?.summary ? (
          <Text mt="xs" c="dimmed">
            {scenario.case.summary}
          </Text>
        ) : null}
      </Box>

      {artifacts.length > 0 ? (
        <Stack gap="sm">
          {artifacts.map((art, i) => (
            <Paper key={i} p="md" radius="md" bg="gray.0" withBorder shadow="paper">
              {art.label ? (
                <Text size="xs" c="dimmed" mb={4} tt="uppercase" fw={600}>
                  {art.label}
                </Text>
              ) : null}
              <Text
                component="pre"
                ff="monospace"
                fz="sm"
                m={0}
                style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
              >
                {art.content}
              </Text>
            </Paper>
          ))}
        </Stack>
      ) : null}

      {!done && current ? (
        <Paper p="lg" radius="xl" withBorder shadow="paper">
          <Text size="sm" c="dimmed" mb="md">
            Step {stepIndex + 1} of {total}
          </Text>
          <Text ff="var(--font-serif)" fw={500} mb="md">
            {current.question}
          </Text>
          <Stack gap="xs">
            {current.options.map((opt, idx) => {
              const graded = currentState.graded;
              const isSelected = currentState.selected === idx;
              const isCorrect = graded && graded.correct_index === idx;
              const isWrong = graded && isSelected && !graded.correct;
              let border = "1px solid var(--mantine-color-default-border)";
              let bg = "var(--mantine-color-gray-0)";
              if (isCorrect) {
                border = "2px solid var(--mantine-color-sage-5)";
                bg = "var(--mantine-color-sage-0)";
              } else if (isWrong) {
                border = "2px solid var(--mantine-color-terracotta-5)";
                bg = "var(--mantine-color-terracotta-0)";
              } else if (isSelected && !graded) {
                border = "2px solid var(--mantine-color-lavender-5)";
                bg = "var(--mantine-color-lavender-0)";
              }
              return (
                <UnstyledButton
                  key={idx}
                  onClick={() => handleSelect(idx)}
                  disabled={Boolean(graded)}
                  p="sm"
                  style={{ border, borderRadius: 12, background: bg, textAlign: "left" }}
                >
                  <Group gap="sm" wrap="nowrap" align="flex-start">
                    <Text fw={600} c="dimmed" w={20}>
                      {String.fromCharCode(65 + idx)}
                    </Text>
                    <Text size="sm" style={{ flex: 1 }}>
                      {opt}
                    </Text>
                    {isCorrect ? <IconCheck size={16} color="var(--mantine-color-sage-6)" /> : null}
                  </Group>
                </UnstyledButton>
              );
            })}
          </Stack>

          {currentState.graded?.explanation ? (
            <Paper mt="md" p="md" radius="md" bg="lavender.0">
              <Group gap="xs" mb={4}>
                <IconBulb size={16} />
                <Text size="sm" fw={600}>
                  Explanation
                </Text>
              </Group>
              <Text size="sm">{currentState.graded.explanation}</Text>
            </Paper>
          ) : null}

          <Group mt="lg" justify="flex-end">
            {!currentState.graded ? (
              <Button
                color="lavender"
                radius="xl"
                disabled={currentState.selected === null}
                onClick={() => void handleSubmitStep()}
              >
                Check answer
              </Button>
            ) : (
              <Button color="lavender" radius="xl" onClick={() => void handleContinue()} loading={reflecting}>
                {stepIndex < total - 1 ? "Next step" : "See coaching"}
              </Button>
            )}
          </Group>
        </Paper>
      ) : null}

      {done && reflectText ? (
        <Paper p="lg" radius="xl" withBorder shadow="paper">
          <Text size="sm" c="dimmed" mb="sm">
            Process coaching
          </Text>
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {reflectText}
          </Text>
        </Paper>
      ) : null}
    </Stack>
  );
}
