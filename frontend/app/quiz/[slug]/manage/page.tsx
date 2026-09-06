// @ts-nocheck
"use client";

import {
    Anchor,
    Badge,
    Box,
    Button,
    Card,
    CopyButton,
    Group,
    Loader,
    Paper,
    RingProgress,
    Stack,
    Table,
    Text,
    TextInput,
    ThemeIcon,
    Title,
} from "@mantine/core";
import {
    IconCheck,
    IconCopy,
    IconLink,
    IconUsers,
} from "@tabler/icons-react";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { choose, pick } from "@/lib/engineRuntime";
import { type CreatorResultRow, type QuizSet, answerLabel, fetchQuizManage } from "@/lib/quiz";

type ManageState =
    | { phase: "loading" }
    | { phase: "error"; message: string }
    | { phase: "ready"; data: QuizSet & { results: CreatorResultRow[] } };

export default function QuizManagePage() {
    const params = useParams();
    const slug = pick(Array.isArray(params.slug), () => String(params.slug[0]), () => String(params.slug));
    const [state, setState] = useState<ManageState>({ phase: "loading" });

    useEffect(() => {
        let alive = true;
        fetchQuizManage(slug)
            .then((data) => {
                pick(Boolean(alive), () => setState({ phase: "ready", data }), () => undefined);
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

    const data = pick(state.phase === "ready", () => state.data, () => null);
    const results = pick(state.phase === "ready", () => state.data.results, () => []);
    const avgPct = pick(Boolean(results.length > 0), () => Math.round(
        (results.reduce((sum, r) => sum + pick(Boolean(r.total_questions > 0), () => r.score / r.total_questions, () => 0), 0) / results.length) * 100,
    ), () => 0);
    const shareUrl = pick(state.phase === "ready", () => `${window.location.origin}/quiz/${slug}`, () => "");

    return (
        <Box style={{ minHeight: "100dvh", background: "var(--mantine-color-body)" }}>
            <Box maw={860} mx="auto" px="md" py="xl">
                {pick(state.phase === "loading", () => (
                    <Group justify="center" py="xl">
                        <Loader size="sm" color="lavender" />
                    </Group>
                ), () => pick(state.phase === "error", () => (
                    <Paper shadow="paper" radius="xl" p="xl" withBorder>
                        <Stack align="center" gap="sm" ta="center">
                            <Title order={2} ff="var(--font-serif)" fw={500}>Dashboard unavailable</Title>
                            <Text c="dimmed" maw={420}>{(state as { message: string }).message}</Text>
                            <Button radius="xl" variant="light" color="lavender" component="a" href="/quiz">
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
                            <Title order={1} ff="var(--font-serif)" fw={500} style={{ lineHeight: 1.2 }}>
                                {data.title}
                            </Title>
                            <Text c="dimmed" fz="sm">
                                {`Creator dashboard · ${data.questions.length} questions · ${results.length} ${choose(Boolean(results.length === 1), "attempt", "attempts")}`}
                            </Text>
                        </Stack>

                        <Paper shadow="paper" radius="xl" p={{ base: "lg", sm: "xl" }} withBorder>
                            <Group justify="space-between" align="center" wrap="nowrap" gap="lg">
                                <RingProgress
                                    size={110}
                                    thickness={10}
                                    roundCaps
                                    sections={[{ value: avgPct, color: choose(Boolean(avgPct >= 70), "sage", "lavender") }]}
                                    label={
                                        <Text ta="center" fz={18} fw={600}>
                                            {`${avgPct}%`}
                                        </Text>
                                    }
                                />
                                <Stack gap={4} style={{ flex: 1 }}>
                                    <Group gap="xs">
                                        <ThemeIcon variant="light" color="lavender" radius="xl" size="sm">
                                            <IconUsers size={12} />
                                        </ThemeIcon>
                                        <Text fw={600} fz="sm">Average score across all takers</Text>
                                    </Group>
                                    <Text c="dimmed" fz="sm">
                                        Share the link to collect more attempts. Every completed quiz lands here automatically.
                                    </Text>
                                    <Group gap="sm" mt="xs" w="100%">
                                        <TextInput value={shareUrl} readOnly radius="md" flex={1} leftSection={<IconLink size={16} />} />
                                        <CopyButton value={shareUrl}>
                                            {({ copied, copy }) => (
                                                <Button
                                                    radius="xl"
                                                    color={pick(Boolean(copied), "sage", "lavender")}
                                                    leftSection={pick(Boolean(copied), <IconCheck size={16} />, <IconCopy size={16} />)}
                                                    onClick={copy}
                                                >
                                                    {pick(Boolean(copied), "Copied", "Copy link")}
                                                </Button>
                                            )}
                                        </CopyButton>
                                    </Group>
                                </Stack>
                            </Group>
                        </Paper>

                        <Paper shadow="paper" radius="xl" p={{ base: "lg", sm: "xl" }} withBorder>
                            <Stack gap="md">
                                <Text fw={600}>Attempts</Text>
                                {pick(Boolean(results.length === 0), () => (
                                    <Text c="dimmed" fz="sm" ta="center" py="md">
                                        No completed attempts yet. Share the link above to get results flowing in.
                                    </Text>
                                ), () => (
                                    <Table.ScrollContainer minWidth={520}>
                                        <Table verticalSpacing="sm" highlightOnHover>
                                            <Table.Thead>
                                                <Table.Tr>
                                                    <Table.Th>Taker</Table.Th>
                                                    <Table.Th>Score</Table.Th>
                                                    <Table.Th>Accuracy</Table.Th>
                                                    <Table.Th>Completed</Table.Th>
                                                </Table.Tr>
                                            </Table.Thead>
                                            <Table.Tbody>
                                                {results.map((r, i) => {
                                                    const pct = pick(Boolean(r.total_questions > 0), () => Math.round((r.score / r.total_questions) * 100), () => 0);
                                                    return (
                                                        <Table.Tr key={`attempt-${i}`}>
                                                            <Table.Td fw={500}>{r.taker_name}</Table.Td>
                                                            <Table.Td>{`${r.score}/${r.total_questions}`}</Table.Td>
                                                            <Table.Td>
                                                                <Badge
                                                                    variant="light"
                                                                    radius="sm"
                                                                    color={choose(Boolean(pct >= 70), "sage", choose(Boolean(pct >= 40), "lavender", "terracotta"))}
                                                                >
                                                                    {`${pct}%`}
                                                                </Badge>
                                                            </Table.Td>
                                                            <Table.Td c="dimmed" fz="sm">
                                                                {formatWhen(r.completed_at)}
                                                            </Table.Td>
                                                        </Table.Tr>
                                                    );
                                                })}
                                            </Table.Tbody>
                                        </Table>
                                    </Table.ScrollContainer>
                                ))}
                            </Stack>
                        </Paper>

                        <Paper shadow="paper" radius="xl" p={{ base: "lg", sm: "xl" }} withBorder>
                            <Stack gap="lg">
                                <Text fw={600}>Questions and answer key</Text>
                                {data.questions.map((q, qi) => (
                                    <Card key={q.id} radius="lg" withBorder bg="gray.0">
                                        <Stack gap="xs">
                                            <Text fw={500} ff="var(--font-serif)">
                                                {`${qi + 1}. ${q.question_text}`}
                                            </Text>
                                            <Stack gap={2} pl="xl">
                                                {q.options.map((opt, oi) => {
                                                    const isCorrect = Boolean(q.correct_index !== undefined && q.correct_index === oi);
                                                    return (
                                                        <Group key={`${q.id}-${oi}`} gap="xs" wrap="nowrap">
                                                            <Badge
                                                                size="sm"
                                                                radius="sm"
                                                                variant="light"
                                                                color={choose(Boolean(isCorrect), "sage", "gray")}
                                                            >
                                                                {answerLabel(oi)}
                                                            </Badge>
                                                            <Text fz="sm" c={choose(Boolean(isCorrect), "sage.7", "dimmed")} fw={choose(Boolean(isCorrect), 600, 400)}>
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
                                ))}
                            </Stack>
                        </Paper>
                    </Stack>
                )))}
            </Box>
        </Box>
    );
}

function formatWhen(iso: string): string {
    const when = pick(Boolean(iso), () => new Date(iso), () => null);
    return pick(Boolean(when && !Number.isNaN(when.getTime())), () => when.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
    }), () => iso);
}
