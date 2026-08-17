// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useEffect, useState } from "react";
import { Badge, Box, Button, Code, Group, Paper, Progress, RingProgress, Select, SimpleGrid, Stack, Text, Textarea, ThemeIcon, UnstyledButton, } from "@mantine/core";
import { IconAlertTriangle, IconArrowRight, IconBriefcase, IconCheck, IconPlayerPlay, IconRefresh, IconTerminal2, IconX, } from "@tabler/icons-react";
import { useInterviewQuery, useInterviewActions, useInterviewLanguagesQuery, type CodeRunResult, type CodingAnswer, type InterviewScores, type InterviewTurn, } from "@/lib/api/queries";
import { McqHeroPanel } from "@/app/workspace/_components/McqPanels";
import { WaitState } from "./WaitState";
const SCORE_LABELS: Record<keyof InterviewScores, string> = {
    problem_framing: "Framing",
    depth: "Depth",
    tradeoffs: "Trade-offs",
    communication: "Communication",
};
const scoreColor = (pct: number) => (choose(Boolean(pct >= 67), "sage", choose(Boolean(pct >= 50), "lavender", "terracotta")));
/**
 * Interview Mode - a live, multi-round mock interview from the learner's resume. Pick a
 * target company category, then answer one question at a time (MCQ or typed); each answer
 * is evaluated before the next question, ending in a per-round + overall report.
 */
export function InterviewView({ artifactId, compact = false }: {
    artifactId: string;
    compact?: boolean;
}) {
    const { data, isError, refetch } = useInterviewQuery(artifactId);
    const { start, answer, reset, runCode } = useInterviewActions(artifactId);
    const { data: langData } = useInterviewLanguagesQuery(artifactId);
    const [phase, setPhase] = useState<"answering" | "feedback">("answering");
    const [selected, setSelected] = useState<string | null>(null);
    const [typed, setTyped] = useState("");
    const [busy, setBusy] = useState(false);
    // coding-round editor state
    const [code, setCode] = useState("");
    const [langId, setLangId] = useState(71);
    const [runResult, setRunResult] = useState<CodeRunResult | null>(null);
    const [running, setRunning] = useState(false);
    // Seed the editor when a new coding question appears (starter code + its language).
    const codingQ = pick(
        Boolean(data?.status === "in_progress" && phase === "answering" && data.current_question?.kind === "coding"),
        () => data.current_question,
        () => null,
    );
    const codingKey = codingQ?.question ?? null;
    useEffect(() => {
        return pick(Boolean(!codingQ), () => {
            return;
        }, () => {
            // eslint-disable-next-line react-hooks/set-state-in-effect -- seed editor state from the fetched coding question
            setCode(codingQ.starter_code ?? "");
            setLangId(codingQ.language_id ?? 71);
            setRunResult(null);
        });
    }, [codingKey]);
    async function run(fn: () => Promise<unknown>, after?: () => void) {
        setBusy(true);
        try {
            await fn();
            after?.();
        }
        finally {
            setBusy(false);
        }
    }
    const doStart = (category: string) => run(() => start(category), () => setPhase("answering"));
    const doSubmit = (value: string | number | CodingAnswer) => run(() => answer(value), () => setPhase("feedback"));
    const doContinue = () => {
        setSelected(null);
        setTyped("");
        setRunResult(null);
        setPhase("answering");
    };
    async function doRun() {
        setRunning(true);
        try {
            setRunResult(await runCode(code, langId));
        }
        catch {
            setRunResult({ status: "Sandbox unavailable", stdout: "", stderr: "Could not reach the code sandbox.", compile_output: "" });
        }
        finally {
            setRunning(false);
        }
    }
    const doReset = () => run(() => reset(), doContinue);
    return pick(Boolean(isError || data?.status === "failed"), () => (<WaitState icon={<IconAlertTriangle size={26}/>} title="Couldn’t run the interview" body="Something went wrong. Try again in a moment." action={<Button variant="light" color="lavender" radius="xl" onClick={() => void refetch()}>Try again</Button>}/>), () => pick(Boolean(!data || data.status === "indexing"), () => <WaitState pet title="Getting your resume ready" body="Indexing your source so we can tailor the interview…"/>, () => pick(Boolean(data.status === "missing"), () => {
        const cats = Object.entries(data.categories ?? {});
        return (<Stack gap="lg" pb="xl">
        <Group gap={8}>
          <ThemeIcon variant="light" color="lavender" radius="xl" size="md"><IconBriefcase size={16}/></ThemeIcon>
          <Text ff="var(--font-serif)" fz={choose(Boolean(compact), 20, 26)} fw={500}>Mock interview</Text>
        </Group>
        <Text c="dimmed" fz="sm">
          We’ll interview you from your resume, one question at a time - MCQs plus system design,
          technical and behavioural rounds - and score each answer. Who are you targeting?
        </Text>
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          {cats.map(([value, meta]) => (<UnstyledButton key={value} onClick={() => void doStart(value)} disabled={busy}>
              <Paper radius="lg" p="md" withBorder style={{
                    borderColor: "var(--app-border, var(--mantine-color-gray-2))",
                    opacity: choose(Boolean(busy), 0.6, 1),
                    transition: "border-color 200ms, transform 200ms",
                }}>
                <Group justify="space-between" wrap="nowrap">
                  <Box>
                    <Text fw={600} fz={choose(Boolean(compact), "sm", "md")}>{meta.label}</Text>
                    <Text c="dimmed" fz="xs" mt={2}>{meta.blurb}</Text>
                  </Box>
                  <IconArrowRight size={18} color="var(--mantine-color-lavender-6)"/>
                </Group>
              </Paper>
            </UnstyledButton>))}
        </SimpleGrid>
      </Stack>);
    }, () => pick(Boolean(data.status === "complete" && phase !== "feedback"), () => {
        const report = data.report;
        return (<Stack gap="lg" pb="xl" align="center">
        <Text ff="var(--font-serif)" fz={choose(Boolean(compact), 22, 28)} fw={500}>Interview complete</Text>
        <RingProgress size={choose(Boolean(compact), 140, 168)} thickness={12} roundCaps sections={[{ value: report?.overall ??
        0, color: scoreColor(report?.overall ?? 0) }]} label={<Text ta="center" ff="var(--font-serif)" fz={choose(Boolean(compact), 30, 38)} fw={600}>
              {report?.overall ?? 0}
            </Text>}/>
        <Text c="dimmed" fz="sm" mt={-8}>Overall readiness</Text>

        <Stack gap="sm" w="100%" maw={560}>
          {(report?.rounds ??
        []).map((r) => (<Paper key={r.name} radius="lg" p="md" withBorder style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))" }}>
              <Group justify="space-between" mb={6}>
                <Text fw={600} fz="sm">{r.name}</Text>
                <Text fw={700} fz="sm" c={`${scoreColor(r.score)}.7`}>{r.score}</Text>
              </Group>
              <Progress value={r.score} color={scoreColor(r.score)} radius="xl" size="sm"/>
              <Text c="dimmed" fz="xs" mt={6}>{r.detail}</Text>
            </Paper>))}
        </Stack>

        {pick(Boolean(report?.strengths?.length || report?.focus_areas?.length), () => (
          <Group gap="xl" w="100%" maw={560} align="flex-start" wrap="wrap">
            {pick(Boolean(report?.strengths?.length), () => (
              <Box style={{ flex: 1, minWidth: 200 }}>
                <Text fz="xs" fw={700} tt="uppercase" c="sage.7" mb={6} style={{ letterSpacing: 0.4 }}>Strengths</Text>
                {report.strengths.map((s) => <Text key={s} fz="sm">• {s}</Text>)}
              </Box>
            ), () => null)}
            {pick(Boolean(report?.focus_areas?.length), () => (
              <Box style={{ flex: 1, minWidth: 200 }}>
                <Text fz="xs" fw={700} tt="uppercase" c="terracotta.7" mb={6} style={{ letterSpacing: 0.4 }}>Focus next</Text>
                {report.focus_areas.map((s) => <Text key={s} fz="sm">• {s}</Text>)}
              </Box>
            ), () => null)}
          </Group>
        ), () => null)}

        <Button variant="light" color="lavender" radius="xl" leftSection={<IconRefresh size={16}/>} loading={busy} onClick={() => void doReset()}>
          New interview
        </Button>
      </Stack>);
    }, () => {
        // -------------------------------------------------------------- feedback (last answered turn)
        const roundNo = data.round_index + 1;
        const progressPct = pick(Boolean(data.total_rounds), () => Math.round((data.round_index / data.total_rounds) * 100), () => 0);
        return pick(Boolean(phase === "feedback"), () => {
            const turn = data.transcript[data.transcript.length - 1];
            const last = data.status === "complete";
            return pick(Boolean(turn?.kind === "mcq" && turn.options), () => (<Stack gap="lg" pb="xl">
          <RoundHeader roundNo={roundNo} total={data.total_rounds} name={turn.round_name ?? ""} pct={progressPct}/>
          <McqHeroPanel stem={turn.question} options={turn.options} selected={pick(Boolean(turn.selected_index != null), () => String(turn.selected_index), () => null)} onSelect={() => {
            }} feedback={turn.feedback ?? null} mcqLoading={false} hasQuestion mode="learn" gradeState={{
                    correct: Boolean(turn.correct),
                    correctIndex: turn.correct_index ?? 0,
                }} submitting={busy} compact={compact} onSubmit={() => {
            }} onContinue={doContinue}/>
          {choose(Boolean(last), (<Group justify="center">
              <Button color="sage" radius="xl" rightSection={<IconArrowRight size={16}/>} loading={busy} onClick={doContinue}>
                See results
              </Button>
            </Group>), null)}
        </Stack>), () => (<Stack gap="lg" pb="xl">
        <RoundHeader roundNo={roundNo} total={data.total_rounds} name={turn?.round_name ?? ""} pct={progressPct}/>
        {choose(Boolean(turn), <FeedbackCard turn={turn} compact={compact}/>, null)}
        <Group justify="flex-end">
          <Button color="lavender" radius="xl" rightSection={<IconArrowRight size={16}/>} loading={busy} onClick={doContinue}>
            {choose(Boolean(last), "See results", "Next question")}
          </Button>
        </Group>
      </Stack>));
        }, () => {
            // -------------------------------------------------------------- answering
            const q = data.current_question;
            return pick(Boolean(!q), () => <WaitState pet title="Preparing the next question" body="Thinking up something good…"/>, () => {
                const isCoding = q.kind === "coding";
                const canSubmit = pick(Boolean(q.kind === "mcq"), () => selected !== null, () => pick(Boolean(isCoding), () => code.trim().length > 0, () => typed.trim().length > 0));
                const submit = () => doSubmit(pick(Boolean(q.kind === "mcq"), () => Number(selected), () => pick(Boolean(isCoding), () => ({ source: code, language_id: langId }), () => typed.trim())));
                const languages = langData?.languages ?? [];
                return pick(Boolean(q.kind === "mcq" && q.options), () => (<Stack gap="lg" pb="xl">
        <RoundHeader roundNo={roundNo} total={data.total_rounds} name={q.round_name} pct={progressPct}/>
        <McqHeroPanel stem={q.question} options={q.options} selected={selected} onSelect={setSelected} feedback={null} mcqLoading={false} hasQuestion mode="learn" gradeState={null} submitting={busy} compact={compact} onSubmit={submit} onContinue={() => {
                }}/>
      </Stack>), () => (<Stack gap="lg" pb="xl">
      <RoundHeader roundNo={roundNo} total={data.total_rounds} name={q.round_name} pct={progressPct}/>
      <Paper radius="lg" p={choose(Boolean(compact), "md", "lg")} withBorder style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))" }}>
        <Text ff="var(--font-serif)" fz={choose(Boolean(compact), 18, 22)} fw={500} lh={1.5} mb="md" style={{ whiteSpace: "pre-wrap" }}>{q.question}</Text>

        {pick(Boolean(isCoding), () => (<Stack gap="sm">
            <Group justify="space-between" wrap="wrap" gap="xs">
              <Select size="xs" radius="md" w={200} value={String(langId)} onChange={(v) => setLangId(Number(v))} data={languages.map((l) => ({ value: String(l.id), label: l.label }))} allowDeselect={false}/>
              {choose(Boolean(q.test_count), <Badge variant="light" color="gray" radius="sm">{q.test_count} hidden tests</Badge>, null)}
            </Group>
            <Textarea value={code} onChange={(e) => setCode(e.currentTarget.value)} placeholder="Read stdin, print the answer to stdout." autosize minRows={10} maxRows={24} spellCheck={false} styles={{ input: { fontFamily: "var(--mantine-font-family-monospace, monospace)", fontSize: 13, lineHeight: 1.55 } }}/>
            <Group>
              <Button size="xs" variant="light" color="lavender" radius="xl" leftSection={<IconPlayerPlay size={14}/>} loading={running} onClick={() => void doRun()}>
                Run
              </Button>
              <Text fz="xs" c="dimmed">Runs your code (no test check) so you can debug before submitting.</Text>
            </Group>
            {choose(Boolean(runResult), <RunOutput result={runResult}/>, null)}
          </Stack>), () => (<Textarea value={typed} onChange={(e) => setTyped(e.currentTarget.value)} placeholder="Type your answer - think out loud, structure it, and cover the trade-offs." autosize minRows={5} radius="md"/>))}
      </Paper>
      <Group justify="flex-end">
        <Button color="lavender" radius="xl" rightSection={<IconArrowRight size={16}/>} disabled={!canSubmit} loading={busy} onClick={submit}>
          {choose(Boolean(isCoding), "Submit & run tests", "Submit answer")}
        </Button>
      </Group>
    </Stack>));
            });
        });
    }))));
}
function RunOutput({ result }: {
    result: CodeRunResult;
}) {
    const err = result.stderr || result.compile_output;
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
function RoundHeader({ roundNo, total, name, pct }: {
    roundNo: number;
    total: number;
    name: string;
    pct: number;
}) {
    return (<Box>
      <Group justify="space-between" mb={6}>
        <Group gap={8}>
          <Badge variant="light" color="lavender" radius="sm">Round {roundNo} / {total}</Badge>
          <Text fw={600} fz="sm">{name}</Text>
        </Group>
      </Group>
      <Progress value={pct} color="lavender" radius="xl" size="xs"/>
    </Box>);
}
function FeedbackCard({ turn, compact }: {
    turn: InterviewTurn;
    compact?: boolean;
}) {
    const border = "var(--app-border, var(--mantine-color-gray-2))";
    return pick(Boolean(turn.kind === "coding"), () => {
        const total = turn.total ?? 0;
        const passed = turn.passed ?? 0;
        const allPass = pick(Boolean(total > 0), () => passed === total, () => total > 0);
        return (<Paper radius="lg" p={choose(Boolean(compact), "md", "lg")} withBorder style={{ borderColor: border }}>
        <Group gap={8} mb="sm">
          <ThemeIcon variant="light" color={choose(Boolean(allPass), "sage", "terracotta")} radius="xl" size="md">
            {choose(Boolean(allPass), <IconCheck size={16}/>, <IconX size={16}/>)}
          </ThemeIcon>
          <Text fw={600} c={choose(Boolean(allPass), "sage.7", "terracotta.7")}>{passed}/{total} tests passed</Text>
        </Group>
        <Text fz="sm" fw={500} mb="xs" style={{ whiteSpace: "pre-wrap" }}>{turn.question}</Text>
        {choose(Boolean(turn.answer), <Code block fz="xs">{turn.answer}</Code>, null)}
        <Text fz="sm" c="dimmed" lh={1.6} mt="sm">{turn.feedback}</Text>
      </Paper>);
    }, () => {
        // MCQ feedback is handled by McqHeroPanel above — this branch is for typed/coding only.
        const scores = turn.scores;
        return (<Paper radius="lg" p={choose(Boolean(compact), "md", "lg")} withBorder style={{ borderColor: border }}>
      <Text fz="sm" fw={500} mb="xs">{turn.question}</Text>
      {choose(Boolean(turn.answer), (<Paper radius="md" p="sm" bg="var(--mantine-color-gray-0)" mb="sm">
          <Text fz="sm" c="dimmed" style={{ whiteSpace: "pre-wrap" }}>{turn.answer}</Text>
        </Paper>), null)}
      {pick(Boolean(scores), () => (<Group gap="xs" mb="sm" wrap="wrap">
          {(Object.keys(SCORE_LABELS) as (keyof InterviewScores)[]).map((k) => {
                    const v = scores[k] ?? 0;
                    const pct = Math.round(((v - 1) / 3) * 100);
                    return (<Badge key={k} variant="light" color={scoreColor(pct)} radius="sm">
                {SCORE_LABELS[k]} {v}/4
              </Badge>);
                })}
        </Group>), () => null)}
      <Text fz="sm" c="dimmed" lh={1.6}>{turn.feedback}</Text>
    </Paper>);
    });
}
