// @ts-nocheck
"use client";

import {
    Anchor,
    Badge,
    Box,
    Button,
    Card,
    CopyButton,
    Divider,
    Group,
    Loader,
    Paper,
    Progress,
    Radio,
    RingProgress,
    Stack,
    Text,
    TextInput,
    ThemeIcon,
    Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
    IconArrowRight,
    IconCheck,
    IconCopy,
    IconLink,
    IconX,
} from "@tabler/icons-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { choose, pick } from "@/lib/engineRuntime";
import {
    type QuizAttemptResult,
    type QuizSet,
    answerLabel,
    fetchQuiz,
    submitQuizAttempt,
} from "@/lib/quiz";

type LoadState =
    | { phase: "loading" }
    | { phase: "error"; message: string }
    | { phase: "ready"; quiz: QuizSet }
    | { phase: "submitting"; quiz: QuizSet }
    | { phase: "result"; quiz: QuizSet; result: QuizAttemptResult };

export default function QuizTakePage() {
    const router = useRouter();
    const params = useParams();
    const slug = pick(Array.isArray(params.slug), () => String(params.slug[0]), () => String(params.slug));
    const [state, setState] = useState<LoadState>({ phase: "loading" });
    const [answers, setAnswers] = useState<Record<string, number>>({});
    const [takerName, setTakerName] = useState("");

    useEffect(() => {
        let alive = true;
        fetchQuiz(slug)
            .then((quiz) => {
                pick(Boolean(alive), () => setState({ phase: "ready", quiz }), () => undefined);
            })
            .catch((err: unknown) => {
                pick(Boolean(alive), () => setState({
                    phase: "error",
                    message: pick(err instanceof Error, () => err.message, () => "This quiz is unavailable."),
                }), () => undefined);
            });
        return () => {
            alive = false;
        };
    }, [slug]);

    const quiz = pick(state.phase === "ready" || state.phase === "submitting" || state.phase === "result", () => state.quiz, () => null);
    const answeredCount = pick(Boolean(quiz), () => Object.keys(answers).length, () => 0);
    const questionCount = pick(Boolean(quiz), () => quiz.questions.length, () => 0);
    const allAnswered = pick(Boolean(quiz), () => quiz.questions.every((q) => answers[q.id] !== undefined), () => false);

    const onSelect = (questionId: string, optionIndex: number) => {
        setAnswers((cur) => ({ ...cur, [questionId]: optionIndex }));
    };

    const onSubmit = async () => {
        pick(Boolean(quiz), () => {
            setState({ phase: "submitting", quiz });
            const ordered = quiz.questions.map((q) => pick(answers[q.id] !== undefined, () => answers[q.id], () => -1));
            submitQuizAttempt(slug, {
                taker_name: pick(Boolean(takerName.trim()), () => takerName.trim(), () => "Anonymous"),
                answers: ordered,
            })
                .then((result) => setState({ phase: "result", quiz, result }))
                .catch((err: unknown) => {
                    setState({ phase: "ready", quiz });
                    notifications.show({
                        title: "Could not submit",
                        message: pick(err instanceof Error, () => err.message, () => "Please try again."),
                        color: "terracotta",
                    });
                });
        }, () => undefined);
    };

    const shareUrl = pick(Boolean(quiz), () => `${window.location.origin}/quiz/${slug}`, () => "");

    return (
        <Box style={{ minHeight: "100dvh", background: "var(--mantine-color-body)" }}>
            <Box maw={760} mx="auto" px="md" py="xl">
                {pick(state.phase === "loading", () => (
                    <Group justify="center" py="xl">
                        <Loader size="sm" color="lavender" />
                    </Group>
                ), () => pick(state.phase === "error", () => (
                    <Paper shadow="paper" radius="xl" p="xl" withBorder>
                        <Stack align="center" gap="sm" ta="center">
                            <Title order={2} ff="var(--font-serif)" fw={500}>Quiz not found</Title>
                            <Text c="dimmed" maw={420}>{(state as { message: string }).message}</Text>
                            <Button radius="xl" variant="light" color="lavender" component={Link} href="/quiz">
                                Build your own quiz
                            </Button>
                        </Stack>
                    </Paper>
                ), () => (
                    <Stack gap="xl">
                        <Stack gap="xs">
                            <Anchor href="/quiz" c="lavender.7" size="sm" underline="never">
                                Question Better.
                            </Anchor>
                            <Group justify="space-between" align="flex-end" wrap="nowrap">
                                <Box>
                                    <Title order={1} ff="var(--font-serif)" fw={500} style={{ lineHeight: 1.2 }}>
                                        {pick(Boolean(quiz), () => quiz.title, () => "")}
                                    </Title>
                                    <Text c="dimmed" fz="sm" mt={4}>
                                        {`By ${pick(Boolean(quiz), () => quiz.creator, () => "Anonymous")} · ${questionCount} questions`}
                                    </Text>
                                </Box>
                                {pick(state.phase === "result", () => (
                                    <Badge variant="light" color="sage" radius="sm" size="lg">
                                        Completed
                                    </Badge>
                                ), () => pick(Boolean(quiz), () => (
                                    <Progress
                                        value={pick(Boolean(questionCount > 0), () => (answeredCount / questionCount) * 100, () => 0)}
                                        w={120}
                                        radius="xl"
                                        color="lavender"
                                    />
                                ), () => null))}
                            </Group>
                            {pick(Boolean(quiz), () => pick(Boolean(quiz.description.length > 0), () => (
                                <Text c="dimmed">{quiz.description}</Text>
                            ), () => null), () => null)}
                        </Stack>

                        {pick(state.phase === "result", () => {
                            const r = (state as { result: QuizAttemptResult }).result;
                            const pct = pick(Boolean(r.total > 0), () => Math.round((r.score / r.total) * 100), () => 0);
                            return (
                                <Paper shadow="paper-lg" radius="xl" p={{ base: "lg", sm: "xl" }} withBorder>
                                    <Stack align="center" gap="md">
                                        <RingProgress
                                            size={140}
                                            thickness={12}
                                            roundCaps
                                            sections={[{ value: pct, color: choose(Boolean(pct >= 70), "sage", "lavender") }]}
                                            label={
                                                <Text ta="center" ff="var(--font-serif)" fw={600} fz={28}>
                                                    {`${r.score}/${r.total}`}
                                                </Text>
                                            }
                                        />
                                        <Text ff="var(--font-serif)" fz={22} fw={500} ta="center">
                                            {verdictLine(pct)}
                                        </Text>
                                        <Text c="dimmed" fz="sm" ta="center" maw={420}>
                                            Every answer below shows the correct choice with an explanation.
                                        </Text>
                                    </Stack>
                                    <Divider my="lg" />
                                    <Stack gap="lg">
                                        {quiz.questions.map((q, qi) => {
                                            const verdict = r.results.find((v) => v.question_id === q.id);
                                            const wasRight = pick(Boolean(verdict), () => verdict.is_correct, () => false);
                                            return (
                                                <Card key={q.id} radius="lg" withBorder bg="gray.0">
                                                    <Stack gap="sm">
                                                        <Group gap="sm" wrap="nowrap" align="flex-start">
                                                            {pick(Boolean(wasRight), () => (
                                                                <ThemeIcon variant="light" color="sage" radius="xl" size="sm" mt={4}>
                                                                    <IconCheck size={12} />
                                                                </ThemeIcon>
                                                            ), () => (
                                                                <ThemeIcon variant="light" color="terracotta" radius="xl" size="sm" mt={4}>
                                                                    <IconX size={12} />
                                                                </ThemeIcon>
                                                            ))}
                                                            <Text fw={500} ff="var(--font-serif)" style={{ flex: 1 }}>
                                                                {`${qi + 1}. ${q.question_text}`}
                                                            </Text>
                                                        </Group>
                                                        <Stack gap={4} pl="xl">
                                                            {q.options.map((opt, oi) => {
                                                                const isCorrect = pick(Boolean(verdict), () => verdict.correct_index === oi, () => false);
                                                                const wasPicked = pick(Boolean(verdict), () => verdict.selected === oi, () => false);
                                                                return (
                                                                    <Group key={`${q.id}-${oi}`} gap="xs" wrap="nowrap">
                                                                        <Badge
                                                                            size="sm"
                                                                            radius="sm"
                                                                            variant="light"
                                                                            color={choose(Boolean(isCorrect), "sage", choose(Boolean(wasPicked), "terracotta", "gray"))}
                                                                        >
                                                                            {answerLabel(oi)}
                                                                        </Badge>
                                                                        <Text fz="sm" c={choose(Boolean(isCorrect), "sage.7", choose(Boolean(wasPicked), "terracotta.7", "dimmed"))} fw={choose(Boolean(isCorrect || wasPicked), 600, 400)}>
                                                                            {opt}
                                                                        </Text>
                                                                    </Group>
                                                                );
                                                            })}
                                                        </Stack>
                                                        {pick(Boolean(q.explanation.length > 0), () => (
                                                            <Text fz="sm" c="dimmed" pl="xl">
                                                                {q.explanation}
                                                            </Text>
                                                        ), () => null)}
                                                    </Stack>
                                                </Card>
                                            );
                                        })}
                                    </Stack>
                                    <Divider my="lg" />
                                    <Stack gap="sm" align="center">
                                        <Text fz="sm" fw={600}>Share this quiz</Text>
                                        <Group gap="sm" w="100%" justify="center">
                                            <TextInput value={shareUrl} readOnly radius="md" w={{ base: "100%", sm: 380 }} leftSection={<IconLink size={16} />} />
                                            <CopyButton value={shareUrl}>
                                                {({ copied, copy }) => (
                                                    <Button
                                                        radius="xl"
                                                        color={pick(Boolean(copied), "sage", "lavender")}
                                                        leftSection={pick(Boolean(copied), <IconCheck size={16} />, <IconCopy size={16} />)}
                                                        onClick={copy}
                                                    >
                                                        {pick(Boolean(copied), "Copied", "Copy")}
                                                    </Button>
                                                )}
                                            </CopyButton>
                                        </Group>
                                        <Button
                                            variant="subtle"
                                            color="gray"
                                            radius="xl"
                                            component={Link}
                                            href={`/quiz/${slug}/manage`}
                                            rightSection={<IconArrowRight size={16} />}
                                        >
                                            Creator dashboard
                                        </Button>
                                        <Button variant="light" color="lavender" radius="xl" onClick={() => router.push("/quiz")}>
                                            Build your own quiz
                                        </Button>
                                    </Stack>
                                </Paper>
                            );
                        }, () => (
                            <Stack gap="lg">
                                {pick(Boolean(quiz), () => quiz.questions.map((q, qi) => (
                                    <Card key={q.id} radius="lg" withBorder>
                                        <Stack gap="sm">
                                            <Text fw={500} ff="var(--font-serif)" fz={17}>
                                                {`${qi + 1}. ${q.question_text}`}
                                            </Text>
                                            <Radio.Group
                                                value={pick(answers[q.id] !== undefined, () => String(answers[q.id]), () => "")}
                                                onChange={(v) => onSelect(q.id, Number(v))}
                                            >
                                                <Stack gap="xs">
                                                    {q.options.map((opt, oi) => (
                                                        <Radio
                                                            key={`${q.id}-${oi}`}
                                                            value={String(oi)}
                                                            color="lavender"
                                                            label={opt}
                                                            radius="md"
                                                        />
                                                    ))}
                                                </Stack>
                                            </Radio.Group>
                                        </Stack>
                                    </Card>
                                )), () => null)}
                                <Paper shadow="paper" radius="xl" p="lg" withBorder>
                                    <Stack gap="md">
                                        <TextInput
                                            label="Your name (optional)"
                                            placeholder="Anonymous"
                                            value={takerName}
                                            onChange={(e) => setTakerName(e.currentTarget.value)}
                                            radius="md"
                                            maxLength={100}
                                        />
                                        <Group justify="space-between">
                                            <Text c="dimmed" fz="sm">
                                                {`${answeredCount} of ${questionCount} answered`}
                                            </Text>
                                            <Button
                                                size="md"
                                                radius="xl"
                                                disabled={!allAnswered}
                                                loading={state.phase === "submitting"}
                                                onClick={() => void onSubmit()}
                                            >
                                                Submit answers
                                            </Button>
                                        </Group>
                                    </Stack>
                                </Paper>
                            </Stack>
                        ))}
                    </Stack>
                )))}
            </Box>
        </Box>
    );
}

function verdictLine(pct: number): string {
    const rules = [
        { min: 100, line: "Flawless. Question better answered by no one." },
        { min: 70, line: "Strong work. The fundamentals are sticking." },
        { min: 40, line: "A solid start. The review below is where the learning lives." },
    ];
    const hit = rules.find((r) => pct >= r.min);
    return pick(Boolean(hit), () => hit.line, () => "Every miss is a map. Read the explanations and try again.");
}
