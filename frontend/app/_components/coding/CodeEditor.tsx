// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Accordion, Box, Button, Code, Group, Paper, ScrollArea, Stack, Text, Textarea, Badge, Select, Tabs, ThemeIcon, Title, UnstyledButton, } from "@mantine/core";
import { IconPlayerPlay, IconTerminal2, IconCheck, IconX, IconFlag, IconArrowRight, IconGripVertical, } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { useCodingLanguagesQuery, useCodingActions, type CodingProblem, type CodeRunResult, type CodingSubmitResult, } from "@/lib/api/queries";
import { CodingAssistPanel } from "@/app/_components/coding/CodingAssistPanel";
import { clampPanel } from "@/app/workspace/_components/studyLayout";
/**
 * Shared coding editor — statement / code / run / submit.
 *
 * - variant="ide": full-viewport LeetCode split (public solve page)
 * - variant="embedded": stacked cards (workspace CodingView)
 */
const LEFT_DEFAULT = 420;
const LEFT_MIN = 280;
const LEFT_MAX = 640;
const CONSOLE_DEFAULT = 220;
const CONSOLE_MIN = 140;
const CONSOLE_MAX = 420;
export function CodeEditor({ problem, compact = false, onSubmitted, variant = "embedded", }: {
    problem: CodingProblem;
    compact?: boolean;
    onSubmitted?: (result: CodingSubmitResult) => void;
    variant?: "ide" | "embedded";
}) {
    const router = useRouter();
    const { data: langData } = useCodingLanguagesQuery();
    const actions = useCodingActions(problem.id);
    const [code, setCode] = useState(problem.starter_code ?? "");
    const [langId, setLangId] = useState<number>(problem.language_id ?? 71);
    const [stdin, setStdin] = useState<string>("");
    const [runResult, setRunResult] = useState<CodeRunResult | null>(null);
    const [running, setRunning] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [submitResult, setSubmitResult] = useState<CodingSubmitResult | null>(null);
    const [consoleTab, setConsoleTab] = useState<string | null>("testcase");
    const [leftTab, setLeftTab] = useState<string | null>("description");
    const [mobileTab, setMobileTab] = useState<string | null>("description");
    const [leftWidth, setLeftWidth] = useState(LEFT_DEFAULT);
    const [consoleHeight, setConsoleHeight] = useState(CONSOLE_DEFAULT);
    const dragRef = useRef<{
        kind: "left" | "console";
        start: number;
        value: number;
    } | null>(null);
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- seed editor state from the fetched problem
        setCode(problem.starter_code ?? "");
        setLangId(problem.language_id ?? 71);
        setRunResult(null);
        setSubmitResult(null);
        setStdin("");
        setConsoleTab("testcase");
    }, [problem.id]);
    const languages = langData?.languages ?? [];
    const lastStatus = submitResult?.error ||
        (choose(Boolean(submitResult), choose(Boolean(submitResult.all_passed), `Submit: ${submitResult.passed}/${submitResult.total} passed`, `Submit: ${submitResult.passed}/${submitResult.total} passed`), null)) ||
        runResult?.status ||
        null;
    async function handleRun() {
        setRunning(true);
        setRunResult(null);
        setConsoleTab("result");
        try {
            setRunResult(await actions.runCode(code, langId, stdin));
        }
        catch (err) {
            setRunResult({
                status: "Sandbox unavailable",
                stdout: "",
                stderr: choose(Boolean(err instanceof Error), err.message, "Could not reach the code sandbox."),
                compile_output: "",
            });
        }
        finally {
            setRunning(false);
        }
    }
    async function handleSubmit() {
        setSubmitting(true);
        setSubmitResult(null);
        setConsoleTab("result");
        try {
            const result = await actions.submit(code, langId);
            setSubmitResult(result);
            onSubmitted?.(result);
        }
        catch (err) {
            setSubmitResult({
                passed: 0,
                total: 0,
                all_passed: false,
                cases: [],
                error: choose(Boolean(err instanceof Error), err.message, "Submit failed."),
                status: "new",
            });
        }
        finally {
            setSubmitting(false);
        }
    }
    function onResizePointerDown(kind: "left" | "console", e: ReactPointerEvent) {
        e.preventDefault();
        (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
        dragRef.current = {
            kind,
            start: choose(Boolean(kind === "left"), e.clientX, e.clientY),
            value: choose(Boolean(kind === "left"), leftWidth, consoleHeight),
        };
    }
    function onResizePointerMove(e: ReactPointerEvent) {
        const drag = dragRef.current;
        return pick(Boolean(!drag), () => {
            return;
        }, () => {
            pick(Boolean(drag.kind === "left"), () => {
                setLeftWidth(clampPanel(drag.value + (e.clientX - drag.start), LEFT_MIN, LEFT_MAX));
            }, () => {
                setConsoleHeight(clampPanel(drag.value - (e.clientY - drag.start), CONSOLE_MIN, CONSOLE_MAX));
            });
        });
    }
    function onResizePointerUp() {
        dragRef.current = null;
    }
    const langSelect = (<Select size="xs" radius="md" w={{ base: "100%", xs: 240 }} value={String(langId)} onChange={(v) => setLangId(Number(v))} data={languages.map((l) => ({ value: String(l.id), label: l.label }))} searchable allowDeselect={false} placeholder="Language"/>);
    const runSubmit = (<Group gap="xs" wrap="wrap">
      <Button size="xs" variant="light" color="lavender" radius="xl" leftSection={<IconPlayerPlay size={14}/>} loading={running} onClick={() => void handleRun()}>
        Run
      </Button>
      <Button size="xs" color="lavender" radius="xl" leftSection={<IconFlag size={14}/>} loading={submitting} disabled={!code.trim()} onClick={() => void handleSubmit()}>
        Submit
      </Button>
    </Group>);
    const descriptionPane = (<DescriptionPane problem={problem} onUseSample={(s) => {
            setStdin(s);
            setConsoleTab("testcase");
            pick(Boolean(variant === "ide" && compact), () => {
                setMobileTab("code");
            }, () => {
            });
        }}/>);
    const assistPane = (<CodingAssistPanel assertionId={problem.id} code={code} languageId={langId} stdin={stdin} lastStatus={lastStatus}/>);
    const editorPane = (<Stack gap={0} h="100%" style={{ minHeight: 0 }}>
      <Group justify="space-between" wrap="wrap" gap="xs" px="sm" py="xs" style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}>
        {langSelect}
        {runSubmit}
      </Group>
      <Box flex={1} style={{ minHeight: 0 }} p="sm">
        <Textarea value={code} onChange={(e) => setCode(e.currentTarget.value)} placeholder="Read stdin, print the answer to stdout." spellCheck={false} h="100%" styles={{
            root: { height: "100%" },
            wrapper: { height: "100%" },
            input: {
                height: "100%",
                fontFamily: "var(--mantine-font-family-monospace, monospace)",
                fontSize: 13,
                lineHeight: 1.55,
                resize: "none",
            },
        }}/>
      </Box>
    </Stack>);
    const consolePane = (<Tabs value={consoleTab} onChange={setConsoleTab} h="100%" styles={{ root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }, panel: { flex: 1, minHeight: 0, overflow: "auto" } }}>
      <Tabs.List px="sm">
        <Tabs.Tab value="testcase">Testcase</Tabs.Tab>
        <Tabs.Tab value="result">Result</Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value="testcase" p="sm">
        <Textarea value={stdin} onChange={(e) => setStdin(e.currentTarget.value)} placeholder="Custom stdin (used by Run, not Submit)" autosize minRows={4} maxRows={10} spellCheck={false} styles={{ input: { fontFamily: "var(--mantine-font-family-monospace, monospace)", fontSize: 12 } }}/>
      </Tabs.Panel>
      <Tabs.Panel value="result" p="sm">
        <Stack gap="sm">
          {choose(Boolean(runResult), <RunOutput result={runResult}/>, null)}
          {choose(Boolean(submitResult), (<SubmitOutput result={submitResult} onPracticeGap={(id) => router.push(`/practice/coding/${id}`)}/>), null)}
          {/*..............................................................................*/choose(Boolean(!runResult && !submitResult), (<Text fz="xs" c="dimmed">Run or submit to see output here.</Text>), null)}
        </Stack>
      </Tabs.Panel>
    </Tabs>);
    return pick(Boolean(variant === "ide" && !compact), () => (<Box h="100%" display="flex" style={{ minHeight: 0 }} onPointerMove={onResizePointerMove} onPointerUp={onResizePointerUp} onPointerCancel={onResizePointerUp}>
        <Box w={leftWidth} h="100%" style={{
            flexShrink: 0,
            borderRight: "1px solid var(--mantine-color-default-border)",
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            background: "var(--mantine-color-gray-0)",
        }}>
          <Tabs value={leftTab} onChange={setLeftTab} h="100%" styles={{
            root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0 },
            panel: { flex: 1, minHeight: 0, overflow: "hidden" },
        }}>
            <Tabs.List px="sm">
              <Tabs.Tab value="description">Description</Tabs.Tab>
              <Tabs.Tab value="assistant">Assistant</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="description">
              <ScrollArea h="100%" px="md" py="sm" offsetScrollbars>
                {descriptionPane}
              </ScrollArea>
            </Tabs.Panel>
            <Tabs.Panel value="assistant" h="100%">
              {assistPane}
            </Tabs.Panel>
          </Tabs>
        </Box>
        <ResizeHandle orientation="vertical" onPointerDown={(e) => onResizePointerDown("left", e)}/>
        <Box flex={1} h="100%" display="flex" style={{ flexDirection: "column", minWidth: 0, minHeight: 0 }}>
          <Box flex={1} style={{ minHeight: 0, overflow: "hidden" }}>
            {editorPane}
          </Box>
          <ResizeHandle orientation="horizontal" onPointerDown={(e) => onResizePointerDown("console", e)}/>
          <Box h={consoleHeight} style={{
            flexShrink: 0,
            borderTop: "1px solid var(--mantine-color-default-border)",
            background: "var(--mantine-color-body)",
            minHeight: 0,
        }}>
            {consolePane}
          </Box>
        </Box>
      </Box>), () => pick(Boolean(variant === "ide" && compact), () => (<Tabs value={mobileTab} onChange={setMobileTab} h="100%" styles={{
            root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0 },
            panel: { flex: 1, minHeight: 0, overflow: "hidden" },
        }}>
        <Tabs.List px="sm">
          <Tabs.Tab value="description">Description</Tabs.Tab>
          <Tabs.Tab value="code">Code</Tabs.Tab>
          <Tabs.Tab value="assistant">Assistant</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="description">
          <ScrollArea h="100%" px="md" py="sm" offsetScrollbars>
            {descriptionPane}
          </ScrollArea>
        </Tabs.Panel>
        <Tabs.Panel value="code">
          <Stack gap={0} h="100%" style={{ minHeight: 0 }}>
            <Box flex={1} style={{ minHeight: 0 }}>{editorPane}</Box>
            <Box h={200} style={{ borderTop: "1px solid var(--mantine-color-default-border)", flexShrink: 0 }}>
              {consolePane}
            </Box>
          </Stack>
        </Tabs.Panel>
        <Tabs.Panel value="assistant" h="100%">
          {assistPane}
        </Tabs.Panel>
      </Tabs>), () => (<Stack gap="md" pb="xl">
      <Paper radius="lg" p={choose(Boolean(compact), "md", "lg")} withBorder style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))" }}>
        {descriptionPane}
      </Paper>
      <Paper radius="lg" p={choose(Boolean(compact), "md", "lg")} withBorder style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))" }}>
        <Stack gap="sm">
          <Group justify="space-between" wrap="wrap" gap="xs">
            {langSelect}
            {runSubmit}
          </Group>
          <Textarea value={code} onChange={(e) => setCode(e.currentTarget.value)} placeholder="Read stdin, print the answer to stdout." autosize minRows={choose(Boolean(compact), 8, 12)} maxRows={24} spellCheck={false} styles={{ input: { fontFamily: "var(--mantine-font-family-monospace, monospace)", fontSize: 13, lineHeight: 1.55 } }}/>
          <Textarea value={stdin} onChange={(e) => setStdin(e.currentTarget.value)} placeholder="Custom stdin (optional - used by Run, not Submit)" autosize minRows={2} maxRows={6} spellCheck={false} styles={{ input: { fontFamily: "var(--mantine-font-family-monospace, monospace)", fontSize: 12 } }}/>
          {choose(Boolean(runResult), <RunOutput result={runResult}/>, null)}
          {choose(Boolean(submitResult), (<SubmitOutput result={submitResult} onPracticeGap={(id) => router.push(`/practice/coding/${id}`)}/>), null)}
        </Stack>
      </Paper>
    </Stack>)));
}
function ResizeHandle({ orientation, onPointerDown, }: {
    orientation: "vertical" | "horizontal";
    onPointerDown: (e: ReactPointerEvent) => void;
}) {
    const vertical = orientation === "vertical";
    return (<Box onPointerDown={onPointerDown} style={{
            flexShrink: 0,
            width: choose(Boolean(vertical), 8, "100%"),
            height: choose(Boolean(vertical), "100%", 8),
            cursor: choose(Boolean(vertical), "col-resize", "row-resize"),
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "var(--mantine-color-body)",
            touchAction: "none",
            userSelect: "none",
        }} aria-hidden>
      <IconGripVertical size={12} color="var(--mantine-color-dimmed)" style={{ transform: choose(Boolean(vertical), undefined, "rotate(90deg)") }}/>
    </Box>);
}
function DescriptionPane({ problem, onUseSample, }: {
    problem: CodingProblem;
    onUseSample: (stdin: string) => void;
}) {
    return (<Stack gap="sm">
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Text ff="var(--font-serif)" fz={22} fw={500}>
          {problem.title}
        </Text>
        <Group gap={6}>
          <DifficultyBadge difficulty={problem.difficulty}/>
          {choose(Boolean(problem.test_count), (<Badge variant="light" color="gray" radius="sm">{problem.test_count} hidden tests</Badge>), null)}
          {choose(Boolean(problem.status === "solved"), (<Badge variant="light" color="sage" radius="sm" leftSection={<IconCheck size={10}/>}>
              Solved
            </Badge>), null)}
        </Group>
      </Group>
      {pick(Boolean(problem.tags?.length), () => (<Group gap={6}>
          {problem.tags.map((t) => (<Badge key={t} variant="light" color="gray" radius="sm" size="sm">{t}</Badge>))}
        </Group>), () => null)}
      <Text fz="sm" lh={1.6} c="var(--mantine-color-text)" style={{ whiteSpace: "pre-wrap" }}>
        {problem.statement}
      </Text>
      {pick(Boolean(problem.sample_tests?.length), () => (<Box>
          <Text fz="xs" c="dimmed" mb={4} tt="uppercase" lts={0.5}>Sample cases</Text>
          <Stack gap={6}>
            {problem.sample_tests.map((t, i) => (<SampleCase key={i} index={i} stdin={t.stdin} expected={t.expected_output} onUse={() => onUseSample(t.stdin)}/>))}
          </Stack>
        </Box>), () => null)}
    </Stack>);
}
function DifficultyBadge({ difficulty }: {
    difficulty: "easy" | "medium" | "hard";
}) {
    const color = choose(Boolean(difficulty === "easy"), "sage", choose(Boolean(difficulty === "hard"), "terracotta", "lavender"));
    return (<Badge variant="light" color={color} radius="sm" tt="capitalize">{difficulty}</Badge>);
}
function SampleCase({ index, stdin, expected, onUse, }: {
    index: number;
    stdin: string;
    expected: string;
    onUse?: () => void;
}) {
    return (<Paper radius="md" p="xs" withBorder style={{ borderColor: "var(--mantine-color-gray-2)" }}>
      <Group gap={6} mb={4} justify="space-between">
        <Text fz="xs" fw={600} c="dimmed">Case {index + 1}</Text>
        {choose(Boolean(onUse), (<UnstyledButton onClick={onUse}>
            <Text fz="xs" c="lavender.7">Use as stdin</Text>
          </UnstyledButton>), null)}
      </Group>
      <Stack gap={2}>
        <Text fz="xs" c="dimmed">Input</Text>
        <Code block fz="xs">{stdin || "(empty)"}</Code>
        <Text fz="xs" c="dimmed" mt={2}>Expected output</Text>
        <Code block fz="xs">{expected || "(empty)"}</Code>
      </Stack>
    </Paper>);
}
function RunOutput({ result }: {
    result: CodeRunResult;
}) {
    const err = result.stderr || result.compile_output;
    // Judge0 calls a clean free-run "Accepted"; we never compared to expected output.
    const status = choose(Boolean((result.status || "").trim().toLowerCase() === "accepted"), "Ran", result.status);
    return (<Box>
      <Group gap={6} mb={4}>
        <IconTerminal2 size={14} color="var(--mantine-color-dimmed)"/>
        <Text fz="xs" c="dimmed">{status}{choose(Boolean(result.time), ` · ${result.time}s`, "")}</Text>
      </Group>
      {choose(Boolean(result.stdout), <Code block fz="xs">{result.stdout}</Code>, null)}
      {choose(Boolean(err), <Code block fz="xs" color="terracotta">{err}</Code>, null)}
      {/*..............................................................................*/choose(Boolean(!result.stdout && !err), <Text fz="xs" c="dimmed">(no output)</Text>, null)}
    </Box>);
}
function SubmitOutput({ result, onPracticeGap, }: {
    result: CodingSubmitResult;
    onPracticeGap: (id: string) => void;
}) {
    const allPassed = result.all_passed;
    const firstFail = result.cases.find((c) => !c.ok);
    const lesson = result.lesson;
    const hasMentor = pick(Boolean(!result.error), () => Boolean(result.mentor_summary || lesson?.title), () => !result.error);
    return (<Stack gap="sm">
      {choose(Boolean(result.error), (<Paper radius="md" p="sm" withBorder style={{ borderColor: "var(--mantine-color-terracotta-3)" }} bg="var(--mantine-color-terracotta-0)">
          <Group gap={6}>
            <ThemeIcon color="terracotta" variant="light" size={20} radius="xl"><IconX size={12}/></ThemeIcon>
            <Text fz="sm" c="terracotta.8">Submission error</Text>
          </Group>
          <Text fz="xs" c="dimmed" mt={4}>{result.error}</Text>
        </Paper>), (<Paper radius="md" p="sm" withBorder bg={choose(Boolean(allPassed), "var(--mantine-color-sage-0)", "var(--mantine-color-terracotta-0)")} style={{ borderColor: choose(Boolean(allPassed), "var(--mantine-color-sage-3)", "var(--mantine-color-terracotta-3)") }}>
          <Group gap={6} mb={choose(Boolean(firstFail), "xs", 0)}>
            <ThemeIcon color={choose(Boolean(allPassed), "sage", "terracotta")} variant="light" size={20} radius="xl">
              {choose(Boolean(allPassed), <IconCheck size={12}/>, <IconX size={12}/>)}
            </ThemeIcon>
            <Text fz="sm" fw={600} c={choose(Boolean(allPassed), "sage.8", "terracotta.8")}>
              {choose(Boolean(allPassed), "All tests passed", `${result.passed} / ${result.total} tests passed`)}
            </Text>
          </Group>
          {/*..............................................................................*/choose(Boolean(firstFail && (firstFail.stdin !== undefined || firstFail.expected !== undefined)), (<Stack gap={2}>
              <Text fz="xs" c="dimmed">First failing case</Text>
              {choose(Boolean(firstFail.stdin !== undefined), (<>
                  <Text fz="xs" c="dimmed" mt={2}>Input</Text>
                  <Code block fz="xs">{firstFail.stdin || "(empty)"}</Code>
                </>), null)}
              <Text fz="xs" c="dimmed" mt={2}>Expected</Text>
              <Code block fz="xs">{firstFail.expected || "(empty)"}</Code>
              <Text fz="xs" c="dimmed" mt={2}>Your output</Text>
              <Code block fz="xs" color="terracotta">{firstFail.stdout || firstFail.stderr || "(no output)"}</Code>
            </Stack>), null)}
        </Paper>))}

      {pick(Boolean(hasMentor), () => (<Paper radius="lg" p="md" withBorder bg="gray.0" shadow="paper">
          <Text size="xs" fw={600} tt="uppercase" lts={1.2} c="lavender.8" mb={6}>
            Mentor
          </Text>
          {choose(Boolean(result.mentor_summary), (<Text ff="var(--font-serif)" fz="md" fw={500} lh={1.45} mb="sm">
              {result.mentor_summary}
            </Text>), null)}
          {choose(Boolean(lesson?.title || lesson?.body), (
            <Box p="sm" mb="sm" bg="lavender.0" style={{ borderRadius: "var(--mantine-radius-md)", border: "1px solid var(--mantine-color-lavender-2)" }}>
              <Text size="xs" fw={600} tt="uppercase" lts={1.2} c="lavender.8" mb={4}>
                Close the gap
              </Text>
              <Title order={5} ff="var(--font-serif)" fw={500} mb={4}>
                {lesson?.title || "Lesson"}
              </Title>
              {choose(Boolean(lesson?.body), (<Text fz="sm" lh={1.6} mb={choose(Boolean(lesson?.try_this), "xs", 0)}>
                  {lesson.body}
                </Text>), null)}
              {choose(Boolean(lesson?.try_this), (<Text fz="sm" fs="italic" c="gray.7">
                  Try this: {lesson.try_this}
                </Text>), null)}
            </Box>), null)}
          {pick(Boolean(result.weak_concepts?.length), () => (<Group gap={6} mb="sm">
              {result.weak_concepts.map((w) => (<Badge key={w} variant="light" color="gray" radius="sm" size="sm">
                  {w}
                </Badge>))}
            </Group>), () => null)}
          {choose(Boolean(result.recommended_next_id), (<Button size="xs" radius="xl" color="lavender" rightSection={<IconArrowRight size={14}/>} onClick={() => onPracticeGap(result.recommended_next_id!)}>
              Practice the gap
            </Button>), null)}
        </Paper>), () => null)}

      {choose(Boolean(result.reference_solution), (<Accordion variant="separated" radius="md">
          <Accordion.Item value="ref">
            <Accordion.Control>
              <Text fw={500} fz="sm">
                Reference solution
              </Text>
            </Accordion.Control>
            <Accordion.Panel>
              <Code block fz="xs">
                {result.reference_solution}
              </Code>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>), null)}
    </Stack>);
}
