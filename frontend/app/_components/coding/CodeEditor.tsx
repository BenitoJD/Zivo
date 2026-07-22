"use client";

import { useEffect, useState } from "react";
import {
  Box,
  Button,
  Code,
  Group,
  Paper,
  Stack,
  Text,
  Textarea,
  Badge,
  Select,
  ThemeIcon,
} from "@mantine/core";
import {
  IconPlayerPlay,
  IconTerminal2,
  IconCheck,
  IconX,
  IconFlag,
} from "@tabler/icons-react";
import {
  useCodingLanguagesQuery,
  useCodingActions,
  type CodingProblem,
  type CodeRunResult,
  type CodingSubmitResult,
} from "@/lib/api/queries";

/**
 * Shared LeetCode-style coding editor - statement panel, language select, code
 * Textarea, Run (debug against custom stdin), and Submit (grade against hidden
 * tests). Used by the workspace Coding study mode and the public sampler.
 *
 * Props:
 * - problem: the public problem payload (sample tests only - hidden tests never
 *   leave the server).
 * - onSubmitted: optional callback after a successful submit (e.g. to refresh
 *   the workspace list's per-problem status).
 * - onWorkspaceInvalidate: optional artifact id to invalidate after submit.
 */
export function CodeEditor({
  problem,
  compact = false,
  onSubmitted,
}: {
  problem: CodingProblem;
  compact?: boolean;
  onSubmitted?: (result: CodingSubmitResult) => void;
}) {
  const { data: langData } = useCodingLanguagesQuery();
  const actions = useCodingActions(problem.id);

  const [code, setCode] = useState(problem.starter_code ?? "");
  const [langId, setLangId] = useState<number>(problem.language_id ?? 71);
  const [stdin, setStdin] = useState<string>("");
  const [runResult, setRunResult] = useState<CodeRunResult | null>(null);
  const [running, setRunning] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<CodingSubmitResult | null>(null);

  // Reseed the editor when the problem changes (different assertion id).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- seed editor state from the fetched problem
    setCode(problem.starter_code ?? "");
    setLangId(problem.language_id ?? 71);
    setRunResult(null);
    setSubmitResult(null);
    setStdin("");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reseed only when the problem id changes
  }, [problem.id]);

  const languages = langData?.languages ?? [];

  async function handleRun() {
    setRunning(true);
    setRunResult(null);
    try {
      setRunResult(await actions.runCode(code, langId, stdin));
    } catch (err) {
      setRunResult({
        status: "Sandbox unavailable",
        stdout: "",
        stderr: err instanceof Error ? err.message : "Could not reach the code sandbox.",
        compile_output: "",
      });
    } finally {
      setRunning(false);
    }
  }

  async function handleSubmit() {
    setSubmitting(true);
    setSubmitResult(null);
    try {
      const result = await actions.submit(code, langId);
      setSubmitResult(result);
      onSubmitted?.(result);
    } catch (err) {
      setSubmitResult({
        passed: 0,
        total: 0,
        all_passed: false,
        cases: [],
        error: err instanceof Error ? err.message : "Submit failed.",
        status: "new",
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Stack gap="md" pb="xl">
      {/* Statement */}
      <Paper radius="lg" p={compact ? "md" : "lg"} withBorder style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))" }}>
        <Group justify="space-between" wrap="wrap" gap="xs" mb="sm">
          <Text ff="var(--font-serif)" fz={compact ? 20 : 24} fw={500}>
            {problem.title}
          </Text>
          <Group gap={6}>
            <DifficultyBadge difficulty={problem.difficulty} />
            {problem.test_count ? (
              <Badge variant="light" color="gray" radius="sm">{problem.test_count} hidden tests</Badge>
            ) : null}
          </Group>
        </Group>
        <Text fz="sm" lh={1.6} c="var(--mantine-color-text)" style={{ whiteSpace: "pre-wrap" }}>
          {problem.statement}
        </Text>
        {problem.sample_tests?.length ? (
          <Box mt="md">
            <Text fz="xs" c="dimmed" mb={4} tt="uppercase" lts={0.5}>Sample cases</Text>
            <Stack gap={6}>
              {problem.sample_tests.map((t, i) => (
                <SampleCase key={i} index={i} stdin={t.stdin} expected={t.expected_output} />
              ))}
            </Stack>
          </Box>
        ) : null}
      </Paper>

      {/* Editor */}
      <Paper radius="lg" p={compact ? "md" : "lg"} withBorder style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))" }}>
        <Stack gap="sm">
          <Group justify="space-between" wrap="wrap" gap="xs">
            <Select
              size="xs"
              radius="md"
              w={200}
              value={String(langId)}
              onChange={(v) => setLangId(Number(v))}
              data={languages.map((l) => ({ value: String(l.id), label: l.label }))}
              allowDeselect={false}
            />
            <Text fz="xs" c="dimmed">{problem.language_label}</Text>
          </Group>
          <Textarea
            value={code}
            onChange={(e) => setCode(e.currentTarget.value)}
            placeholder="Read stdin, print the answer to stdout."
            autosize
            minRows={compact ? 8 : 12}
            maxRows={24}
            spellCheck={false}
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace, monospace)", fontSize: 13, lineHeight: 1.55 } }}
          />
          {/* Custom stdin for the Run button */}
          <Textarea
            value={stdin}
            onChange={(e) => setStdin(e.currentTarget.value)}
            placeholder="Custom stdin (optional - used by Run, not Submit)"
            autosize
            minRows={2}
            maxRows={6}
            spellCheck={false}
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace, monospace)", fontSize: 12 } }}
          />
          <Group>
            <Button
              size="xs"
              variant="light"
              color="lavender"
              radius="xl"
              leftSection={<IconPlayerPlay size={14} />}
              loading={running}
              onClick={() => void handleRun()}
            >
              Run
            </Button>
            <Button
              size="xs"
              color="lavender"
              radius="xl"
              leftSection={<IconFlag size={14} />}
              loading={submitting}
              disabled={!code.trim()}
              onClick={() => void handleSubmit()}
            >
              Submit &amp; run tests
            </Button>
          </Group>
          {runResult ? <RunOutput result={runResult} /> : null}
          {submitResult ? <SubmitOutput result={submitResult} /> : null}
        </Stack>
      </Paper>
    </Stack>
  );
}

function DifficultyBadge({ difficulty }: { difficulty: "easy" | "medium" | "hard" }) {
  const color = difficulty === "easy" ? "sage" : difficulty === "hard" ? "terracotta" : "lavender";
  return (
    <Badge variant="light" color={color} radius="sm" tt="capitalize">{difficulty}</Badge>
  );
}

function SampleCase({ index, stdin, expected }: { index: number; stdin: string; expected: string }) {
  return (
    <Paper radius="md" p="xs" withBorder style={{ borderColor: "var(--mantine-color-gray-2)" }}>
      <Group gap={6} mb={4}>
        <Text fz="xs" fw={600} c="dimmed">Case {index + 1}</Text>
      </Group>
      <Stack gap={2}>
        <Text fz="xs" c="dimmed">Input</Text>
        <Code block fz="xs">{stdin || "(empty)"}</Code>
        <Text fz="xs" c="dimmed" mt={2}>Expected output</Text>
        <Code block fz="xs">{expected || "(empty)"}</Code>
      </Stack>
    </Paper>
  );
}

function RunOutput({ result }: { result: CodeRunResult }) {
  const err = result.stderr || result.compile_output;
  return (
    <Box>
      <Group gap={6} mb={4}>
        <IconTerminal2 size={14} color="var(--mantine-color-dimmed)" />
        <Text fz="xs" c="dimmed">{result.status}{result.time ? ` · ${result.time}s` : ""}</Text>
      </Group>
      {result.stdout ? <Code block fz="xs">{result.stdout}</Code> : null}
      {err ? <Code block fz="xs" color="terracotta">{err}</Code> : null}
      {!result.stdout && !err ? <Text fz="xs" c="dimmed">(no output)</Text> : null}
    </Box>
  );
}

function SubmitOutput({ result }: { result: CodingSubmitResult }) {
  if (result.error) {
    return (
      <Paper radius="md" p="sm" withBorder style={{ borderColor: "var(--mantine-color-terracotta-3)" }} bg="var(--mantine-color-terracotta-0)">
        <Group gap={6}>
          <ThemeIcon color="terracotta" variant="light" size={20} radius="xl"><IconX size={12} /></ThemeIcon>
          <Text fz="sm" c="terracotta.8">Submission error</Text>
        </Group>
        <Text fz="xs" c="dimmed" mt={4}>{result.error}</Text>
      </Paper>
    );
  }
  const allPassed = result.all_passed;
  const firstFail = result.cases.find((c) => !c.ok);
  return (
    <Paper
      radius="md"
      p="sm"
      withBorder
      bg={allPassed ? "var(--mantine-color-sage-0)" : "var(--mantine-color-terracotta-0)"}
      style={{ borderColor: allPassed ? "var(--mantine-color-sage-3)" : "var(--mantine-color-terracotta-3)" }}
    >
      <Group gap={6} mb={firstFail ? "xs" : 0}>
        <ThemeIcon color={allPassed ? "sage" : "terracotta"} variant="light" size={20} radius="xl">
          {allPassed ? <IconCheck size={12} /> : <IconX size={12} />}
        </ThemeIcon>
        <Text fz="sm" fw={600} c={allPassed ? "sage.8" : "terracotta.8"}>
          {allPassed ? "All tests passed" : `${result.passed} / ${result.total} tests passed`}
        </Text>
      </Group>
      {firstFail && (firstFail.stdin !== undefined || firstFail.expected !== undefined) ? (
        <Stack gap={2}>
          <Text fz="xs" c="dimmed">First failing case</Text>
          {firstFail.stdin !== undefined ? (
            <>
              <Text fz="xs" c="dimmed" mt={2}>Input</Text>
              <Code block fz="xs">{firstFail.stdin || "(empty)"}</Code>
            </>
          ) : null}
          <Text fz="xs" c="dimmed" mt={2}>Expected</Text>
          <Code block fz="xs">{firstFail.expected || "(empty)"}</Code>
          <Text fz="xs" c="dimmed" mt={2}>Your output</Text>
          <Code block fz="xs" color="terracotta">{firstFail.stdout || firstFail.stderr || "(no output)"}</Code>
        </Stack>
      ) : null}
    </Paper>
  );
}
