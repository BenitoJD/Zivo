// @ts-nocheck
"use client";

import {
    Badge,
    Box,
    Button,
    Card,
    CopyButton,
    Fieldset,
    Group,
    NumberInput,
    Paper,
    Radio,
    SegmentedControl,
    Stack,
    Text,
    TextInput,
    Textarea,
    ThemeIcon,
    Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
    IconCheck,
    IconCopy,
    IconLink,
    IconPlus,
    IconSparkles,
    IconTrash,
    IconWand,
} from "@tabler/icons-react";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { errorMessage, pick } from "@/lib/engineRuntime";
import {
    type CreatedQuiz,
    type DraftQuestion,
    answerLabel,
    createQuiz,
    fixQuizQuestions,
    generateQuizQuestions,
} from "@/lib/quiz";

const EMPTY_DRAFT: DraftQuestion = {
    question_text: "",
    options: ["", "", "", ""],
    correct_index: 0,
    explanation: "",
};

function draftIsUsable(drafts: DraftQuestion[]): boolean {
    return drafts.every((q) => Boolean(q.question_text.trim()) && q.options.every((o) => Boolean(o.trim())));
}

export default function QuizCreatePage() {
    const router = useRouter();
    const [title, setTitle] = useState("");
    const [description, setDescription] = useState("");
    const [creatorName, setCreatorName] = useState("");
    const [drafts, setDrafts] = useState<DraftQuestion[]>([]);
    const [topic, setTopic] = useState("");
    const [count, setCount] = useState(5);
    const [difficulty, setDifficulty] = useState("medium");
    const [generating, setGenerating] = useState(false);
    const [fixing, setFixing] = useState(false);
    const [creating, setCreating] = useState(false);
    const [created, setCreated] = useState<CreatedQuiz | null>(null);

    const setDraft = (index: number, patch: Partial<DraftQuestion>) => {
        setDrafts((cur) => cur.map((q, i) => pick(Boolean(i === index), () => ({ ...q, ...patch }), () => q)));
    };

    const setOption = (qIndex: number, oIndex: number, value: string) => {
        setDrafts((cur) => cur.map((q, i) => pick(Boolean(i === qIndex), () => ({
            ...q,
            options: q.options.map((o, j) => pick(Boolean(j === oIndex), () => value, () => o)),
        }), () => q)));
    };

    const removeDraft = (index: number) => {
        setDrafts((cur) => cur.filter((_, i) => i !== index));
    };

    const onGenerate = async () => {
        setGenerating(true);
        try {
            const res = await generateQuizQuestions({ topic, count, difficulty });
            setDrafts((cur) => [...cur, ...res.questions]);
            notifications.show({
                title: "Questions ready",
                message: `Generated ${res.questions.length} questions on "${topic}".`,
                color: "sage",
            });
        }
        catch (err) {
            notifications.show({
                title: "Generation failed",
                message: errorMessage(err, "Please try again."),
                color: "terracotta",
            });
        }
        finally {
            setGenerating(false);
        }
    };

    const onFix = async () => {
        setFixing(true);
        try {
            const res = await fixQuizQuestions(drafts);
            setDrafts(res.questions);
            notifications.show({ title: "Polished", message: "Questions cleaned up.", color: "sage" });
        }
        catch (err) {
            notifications.show({
                title: "Fix failed",
                message: errorMessage(err, "Please try again."),
                color: "terracotta",
            });
        }
        finally {
            setFixing(false);
        }
    };

    const onCreate = async () => {
        setCreating(true);
        try {
            const res = await createQuiz({
                title: title.trim(),
                description: description.trim(),
                creator_name: pick(Boolean(creatorName.trim()), () => creatorName.trim(), () => "Anonymous"),
                questions: drafts.map((q) => ({ ...q, question_text: q.question_text.trim() })),
            });
            setCreated(res);
            notifications.show({
                title: "Quiz live",
                message: "Share the link with anyone.",
                color: "sage",
            });
        }
        catch (err) {
            notifications.show({
                title: "Could not create quiz",
                message: errorMessage(err, "Please try again."),
                color: "terracotta",
            });
        }
        finally {
            setCreating(false);
        }
    };

    const shareUrl = pick(Boolean(created), () => `${window.location.origin}/quiz/${created?.slug ?? ""}`, () => "");
    const titleReady = title.trim().length > 0;
    const draftsReady = drafts.length > 0;
    const publishBlocked = pick(Boolean(titleReady && draftsReady), () => !draftIsUsable(drafts), () => true);

    return (
        <Box style={{ minHeight: "100dvh", background: "var(--mantine-color-body)" }}>
            <Container_>
                {pick(Boolean(created), () => (
                    <Paper shadow="paper-lg" radius="xl" p={{ base: "xl", sm: 48 }} withBorder>
                        <Stack align="center" gap="md" ta="center">
                            <ThemeIcon variant="light" color="sage" size={56} radius="xl">
                                <IconCheck size={28} />
                            </ThemeIcon>
                            <Title order={1} ff="var(--font-serif)" fw={500}>
                                {`"${created?.title}" is live`}
                            </Title>
                            <Text c="dimmed" maw={420}>
                                {`${created?.question_count ?? 0} questions. Anyone with the link can take it, and every finished attempt lands on your dashboard.`}
                            </Text>
                            <TextInput
                                value={shareUrl}
                                readOnly
                                w={{ base: "100%", sm: 460 }}
                                radius="md"
                                leftSection={<IconLink size={16} />}
                            />
                            <Group gap="sm" mt="xs">
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
                                <Button
                                    radius="xl"
                                    variant="light"
                                    color="lavender"
                                    onClick={() => router.push(`/quiz/${created?.slug}`)}
                                >
                                    Preview quiz
                                </Button>
                                <Button
                                    radius="xl"
                                    variant="subtle"
                                    color="gray"
                                    onClick={() => router.push(`/quiz/${created?.slug}/manage`)}
                                >
                                    Results dashboard
                                </Button>
                            </Group>
                        </Stack>
                    </Paper>
                ), () => (
                    <Stack gap="xl">
                        <Stack gap="xs">
                            <Badge variant="light" color="lavender" radius="sm" w="fit-content">
                                Quiz Share
                            </Badge>
                            <Title order={1} ff="var(--font-serif)" fw={500} style={{ lineHeight: 1.2 }}>
                                Build a quiz. Share the link.
                            </Title>
                            <Text c="dimmed" maw={520}>
                                Write your own questions or let AI draft them. Anyone with the link can take the
                                quiz, and every result flows back to your dashboard.
                            </Text>
                        </Stack>

                        <Paper shadow="paper" radius="xl" p={{ base: "lg", sm: "xl" }} withBorder>
                            <Stack gap="md">
                                <TextInput
                                    label="Quiz title"
                                    placeholder="Photosynthesis fundamentals"
                                    value={title}
                                    onChange={(e) => setTitle(e.currentTarget.value)}
                                    radius="md"
                                    required
                                    maxLength={200}
                                />
                                <Textarea
                                    label="Description (optional)"
                                    placeholder="What this quiz covers and who it is for."
                                    value={description}
                                    onChange={(e) => setDescription(e.currentTarget.value)}
                                    radius="md"
                                    autosize
                                    minRows={2}
                                    maxRows={4}
                                    maxLength={2000}
                                />
                                <TextInput
                                    label="Your name"
                                    placeholder="Anonymous"
                                    value={creatorName}
                                    onChange={(e) => setCreatorName(e.currentTarget.value)}
                                    radius="md"
                                    maxLength={100}
                                />
                            </Stack>
                        </Paper>

                        <Paper shadow="paper" radius="xl" p={{ base: "lg", sm: "xl" }} withBorder>
                            <Stack gap="md">
                                <Group gap="sm">
                                    <ThemeIcon variant="light" color="lavender" radius="xl">
                                        <IconSparkles size={16} />
                                    </ThemeIcon>
                                    <Text fw={600}>Draft questions with AI</Text>
                                </Group>
                                <Group align="flex-end" gap="sm" w="100%">
                                    <TextInput
                                        label="Topic"
                                        placeholder="The water cycle"
                                        value={topic}
                                        onChange={(e) => setTopic(e.currentTarget.value)}
                                        radius="md"
                                        style={{ flex: 2, minWidth: 200 }}
                                        grow
                                    />
                                    <NumberInput
                                        label="Questions"
                                        value={count}
                                        onChange={(v) => setCount(pick(Boolean(Number(v) >= 1), () => Number(v), () => 1))}
                                        min={1}
                                        max={20}
                                        radius="md"
                                        w={110}
                                    />
                                    <SegmentedControl
                                        value={difficulty}
                                        onChange={setDifficulty}
                                        radius="xl"
                                        data={[
                                            { label: "Easy", value: "easy" },
                                            { label: "Medium", value: "medium" },
                                            { label: "Hard", value: "hard" },
                                        ]}
                                    />
                                    <Button
                                        radius="xl"
                                        loading={generating}
                                        disabled={pick(Boolean(topic.trim().length > 0), () => drafts.length >= 40, () => true)}
                                        onClick={() => void onGenerate()}
                                        leftSection={<IconSparkles size={16} />}
                                    >
                                        Generate
                                    </Button>
                                </Group>
                            </Stack>
                        </Paper>

                        <Fieldset
                            radius="xl"
                            legend={
                                <Group gap="xs">
                                    <Text fw={600} fz="sm">{`Questions (${drafts.length})`}</Text>
                                    {pick(Boolean(drafts.length > 0), () => (
                                        <Button
                                            size="compact-xs"
                                            variant="light"
                                            color="lavender"
                                            radius="xl"
                                            leftSection={<IconWand size={12} />}
                                            loading={fixing}
                                            onClick={() => void onFix()}
                                        >
                                            Fix with AI
                                        </Button>
                                    ), () => null)}
                                </Group>
                            }
                        >
                            <Stack gap="lg">
                                {pick(Boolean(drafts.length === 0), () => (
                                    <Text c="dimmed" fz="sm" ta="center" py="md">
                                        No questions yet. Generate a set above, or add one manually.
                                    </Text>
                                ), () => drafts.map((q, qi) => (
                                    <Card key={`draft-${qi}`} radius="lg" withBorder bg="gray.0">
                                        <Stack gap="sm">
                                            <Group align="flex-start" gap="sm" wrap="nowrap">
                                                <Badge variant="light" color="lavender" radius="sm" mt={8}>
                                                    {qi + 1}
                                                </Badge>
                                                <Textarea
                                                    placeholder="Question text"
                                                    value={q.question_text}
                                                    onChange={(e) => setDraft(qi, { question_text: e.currentTarget.value })}
                                                    radius="md"
                                                    autosize
                                                    minRows={1}
                                                    flex={1}
                                                />
                                                <ActionRemove onRemove={() => removeDraft(qi)} />
                                            </Group>
                                            <Radio.Group
                                                value={String(q.correct_index)}
                                                onChange={(v) => setDraft(qi, { correct_index: Number(v) })}
                                            >
                                                <Stack gap="xs">
                                                    {q.options.map((opt, oi) => (
                                                        <Group key={`opt-${qi}-${oi}`} gap="xs" wrap="nowrap">
                                                            <Radio value={String(oi)} color="sage" aria-label={`Mark option ${oi + 1} correct`} />
                                                            <TextInput
                                                                placeholder={`Option ${answerLabel(oi)}`}
                                                                value={opt}
                                                                onChange={(e) => setOption(qi, oi, e.currentTarget.value)}
                                                                radius="md"
                                                                flex={1}
                                                            />
                                                        </Group>
                                                    ))}
                                                </Stack>
                                            </Radio.Group>
                                            <TextInput
                                                label="Explanation (shown after the quiz)"
                                                placeholder="Why is the marked answer correct?"
                                                value={q.explanation}
                                                onChange={(e) => setDraft(qi, { explanation: e.currentTarget.value })}
                                                radius="md"
                                            />
                                        </Stack>
                                    </Card>
                                )))}
                                <Button
                                    variant="light"
                                    color="lavender"
                                    radius="xl"
                                    leftSection={<IconPlus size={16} />}
                                    w="fit-content"
                                    onClick={() => setDrafts((cur) => [...cur, { ...EMPTY_DRAFT, options: [...EMPTY_DRAFT.options] }])}
                                >
                                    Add question manually
                                </Button>
                            </Stack>
                        </Fieldset>

                        <Group justify="flex-end" pb="xl">
                            <Button
                                size="md"
                                radius="xl"
                                disabled={publishBlocked}
                                loading={creating}
                                onClick={() => void onCreate()}
                            >
                                Publish quiz
                            </Button>
                        </Group>
                    </Stack>
                ))}
            </Container_>
        </Box>
    );
}

function Container_({ children }: { children: React.ReactNode }) {
    return (
        <Box maw={760} mx="auto" px="md" py="xl">
            {children}
        </Box>
    );
}

function ActionRemove({ onRemove }: { onRemove: () => void }) {
    return (
        <Button
            variant="subtle"
            color="terracotta"
            radius="xl"
            px="xs"
            aria-label="Remove question"
            onClick={onRemove}
        >
            <IconTrash size={16} />
        </Button>
    );
}
