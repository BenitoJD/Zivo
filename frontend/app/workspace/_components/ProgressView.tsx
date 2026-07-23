"use client";

import Link from "next/link";
import {
  Badge,
  Box,
  Button,
  Group,
  Loader,
  Paper,
  Progress,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import {
  IconArrowRight,
  IconBulb,
  IconChartBar,
  IconCheck,
  IconMessageCircle,
  IconX,
} from "@tabler/icons-react";
import { sourceLabel } from "@/app/workspace/_components/Sidebar";
import { WaitState } from "@/app/workspace/_components/WaitState";
import { useLearnerProgressQuery, type LearnerProgress } from "@/lib/api/queries";

function pct(correct: number, total: number) {
  if (total <= 0) return null;
  return Math.round((correct / total) * 100);
}

function StatFigure({
  value,
  label,
  color,
  icon,
}: {
  value: string | number;
  label: string;
  color?: string;
  icon?: React.ReactNode;
}) {
  return (
    <Group gap={10} wrap="nowrap" align="flex-start">
      {icon ? (
        <ThemeIcon size={34} radius="xl" variant="light" color={color ?? "gray"}>
          {icon}
        </ThemeIcon>
      ) : null}
      <Box>
        <Text
          fz={26}
          fw={600}
          lh={1.1}
          c="var(--mantine-color-text)"
          style={{ fontFamily: "var(--font-serif), Georgia, serif", fontVariantNumeric: "tabular-nums" }}
        >
          {value}
        </Text>
        <Text fz="xs" c="dimmed" mt={4}>
          {label}
        </Text>
      </Box>
    </Group>
  );
}

function RecentActivity({ recent }: { recent: LearnerProgress["recent"] }) {
  if (recent.length === 0) return null;
  const maxAnswered = Math.max(1, ...recent.map((d) => d.answered + d.questions_asked));
  return (
    <Stack gap="sm">
      <Text size="xs" tt="uppercase" fw={700} c="dimmed" style={{ letterSpacing: "0.1em" }}>
        Recent days
      </Text>
      <Stack gap={8}>
        {recent.map((d) => {
          const activity = d.answered + d.questions_asked;
          const width = Math.max(6, Math.round((activity / maxAnswered) * 100));
          const dayLabel = new Date(`${d.day}T12:00:00Z`).toLocaleDateString(undefined, {
            weekday: "short",
            month: "short",
            day: "numeric",
          });
          return (
            <Box key={d.day}>
              <Group justify="space-between" gap="sm" mb={4} wrap="nowrap">
                <Text fz="sm" c="var(--mantine-color-text)">
                  {dayLabel}
                </Text>
                <Text fz="xs" c="dimmed" style={{ fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>
                  {d.answered} answered
                  {d.questions_asked > 0 ? ` · ${d.questions_asked} asked` : ""}
                </Text>
              </Group>
              <Box
                h={6}
                bg="var(--mantine-color-default-hover)"
                style={{ borderRadius: 999, overflow: "hidden" }}
              >
                <Box
                  h="100%"
                  w={`${width}%`}
                  bg="var(--mantine-color-lavender-4)"
                  style={{ borderRadius: 999 }}
                />
              </Box>
            </Box>
          );
        })}
      </Stack>
    </Stack>
  );
}

function TopicCoverage({
  topics,
  onStudyConcept,
}: {
  topics: LearnerProgress["topics"];
  onStudyConcept?: (concept: string) => void;
}) {
  if (topics.length === 0) return null;
  const weak = topics
    .filter((t) => t.correct < t.total)
    .sort((a, b) => a.correct / a.total - b.correct / b.total);
  const strong = topics.filter((t) => t.correct === t.total && t.total > 0);
  return (
    <Stack gap="md">
      {weak.length > 0 ? (
        <Stack gap={8}>
          <Text fz="sm" fw={600} c="var(--mantine-color-text)">
            Concepts to revisit
          </Text>
          {weak.slice(0, 8).map((t) => (
            <Box key={t.concept}>
              <Group justify="space-between" gap="sm" wrap="nowrap" mb={3} align="flex-start">
                <Text fz="sm" c="var(--mantine-color-text)" style={{ minWidth: 0 }}>
                  {t.concept}
                </Text>
                <Group gap={8} wrap="nowrap" style={{ flexShrink: 0 }}>
                  <Text fz="xs" c="dimmed" style={{ fontVariantNumeric: "tabular-nums" }}>
                    {t.correct}/{t.total} first try
                  </Text>
                  {onStudyConcept ? (
                    <Button
                      size="compact-xs"
                      variant="light"
                      color="lavender"
                      radius="xl"
                      onClick={() => onStudyConcept(t.concept)}
                    >
                      Study this
                    </Button>
                  ) : null}
                </Group>
              </Group>
              <Progress
                value={Math.round((t.correct / Math.max(t.total, 1)) * 100)}
                color="terracotta"
                size="sm"
                radius="xl"
              />
            </Box>
          ))}
        </Stack>
      ) : null}
      {strong.length > 0 ? (
        <Stack gap={6}>
          <Text fz="sm" fw={600} c="var(--mantine-color-text)">
            Steady concepts
          </Text>
          <Group gap={6}>
            {strong.slice(0, 12).map((t) => (
              <Badge
                key={t.concept}
                radius="sm"
                tt="none"
                styles={{
                  root: {
                    background: "var(--mantine-color-sage-2)",
                    color: "var(--mantine-color-sage-9)",
                    border: "1px solid var(--mantine-color-sage-4)",
                    fontWeight: 600,
                  },
                }}
              >
                {t.concept}
              </Badge>
            ))}
          </Group>
        </Stack>
      ) : null}
    </Stack>
  );
}

function SourceLedger({
  sources,
  highlightId,
}: {
  sources: LearnerProgress["sources"];
  highlightId?: string | null;
}) {
  if (sources.length === 0) return null;
  return (
    <Stack gap="sm">
      <Text size="xs" tt="uppercase" fw={700} c="dimmed" style={{ letterSpacing: "0.1em" }}>
        By source
      </Text>
      <Stack gap={6}>
        {sources.map((s) => {
          const accuracy = pct(s.correct, s.total);
          const active = highlightId === s.artifact_id;
          return (
            <Paper
              key={s.artifact_id}
              component={Link}
              href={`/workspace/${s.artifact_id}`}
              withBorder
              radius="lg"
              p="md"
              bg={active ? "lavender.0" : "gray.0"}
              shadow="paper"
              style={{ textDecoration: "none", display: "block" }}
            >
              <Group justify="space-between" align="flex-start" wrap="nowrap" gap="md">
                <Box style={{ minWidth: 0 }}>
                  <Text fw={600} fz="sm" c="var(--mantine-color-text)" truncate>
                    {sourceLabel(s.title)}
                  </Text>
                  <Text fz="xs" c="dimmed" mt={4}>
                    {s.total} answered · {s.questions_asked} asked
                  </Text>
                </Box>
                <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
                  <Text
                    fz="lg"
                    fw={600}
                    c="var(--mantine-color-text)"
                    style={{
                      fontFamily: "var(--font-serif), Georgia, serif",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {accuracy == null ? "—" : `${accuracy}%`}
                  </Text>
                  <IconArrowRight size={16} stroke={1.6} color="var(--mantine-color-dimmed)" />
                </Group>
              </Group>
            </Paper>
          );
        })}
      </Stack>
    </Stack>
  );
}

/**
 * Calm study journal: lifetime or per-source first-try accuracy, questions asked,
 * recent days with real activity, and concept coverage. No fabricated charts.
 */
export function ProgressView({
  artifactId,
  compact = false,
  onStartLearn,
  onStudyConcept,
  onAddSource,
}: {
  /** When set, scopes the journal to one source (workspace Progress mode). */
  artifactId?: string | null;
  compact?: boolean;
  /** Switch into Learn for this source (preferred over a same-page Link). */
  onStartLearn?: () => void;
  /** Weak-concept CTA: set focus + jump to Learn. */
  onStudyConcept?: (concept: string) => void;
  onAddSource?: () => void;
}) {
  const { data, isLoading, isError, refetch } = useLearnerProgressQuery(artifactId ?? null);
  const scoped = Boolean(artifactId);
  const empty = data && data.answers.total === 0 && data.questions_asked === 0;

  if (isLoading) {
    return (
      <Group justify="center" py="xl">
        <Loader color="lavender" size="sm" />
      </Group>
    );
  }

  if (isError || !data) {
    return (
      <WaitState
        icon={<IconChartBar size={26} />}
        title="Couldn’t load progress"
        body="Your study journal didn’t come through. Try again in a moment."
        action={
          <Button variant="light" color="lavender" radius="xl" onClick={() => void refetch()}>
            Retry
          </Button>
        }
      />
    );
  }

  if (empty) {
    const emptyAction = scoped ? (
      onStartLearn ? (
        <Button color="lavender" radius="xl" leftSection={<IconBulb size={16} />} onClick={onStartLearn}>
          Start Learn
        </Button>
      ) : (
        <Button
          component={Link}
          href={artifactId ? `/workspace/${artifactId}` : "/workspace"}
          color="lavender"
          radius="xl"
          leftSection={<IconBulb size={16} />}
        >
          Start Learn
        </Button>
      )
    ) : onAddSource ? (
      <Button color="lavender" radius="xl" leftSection={<IconBulb size={16} />} onClick={onAddSource}>
        Add a source
      </Button>
    ) : (
      <Button component={Link} href="/workspace" color="lavender" radius="xl" leftSection={<IconBulb size={16} />}>
        Go to library
      </Button>
    );

    return (
      <WaitState
        icon={<IconChartBar size={26} />}
        title={scoped ? "No answers here yet" : "Your journal is empty"}
        body={
          scoped
            ? "Answer a few Learn questions in this source — first tries land here."
            : "Practice from a source and ask the tutor. Progress fills in from real answers."
        }
        action={emptyAction}
      />
    );
  }

  const accuracy = pct(data.answers.correct, data.answers.total);
  const hasToday = data.today.answered > 0 || data.today.questions_asked > 0;

  return (
    <Box
      maw={640}
      mx="auto"
      w="100%"
      px={compact ? "md" : { base: "md", sm: "lg" }}
      py={compact ? "md" : { base: "md", sm: "xl" }}
    >
      <Stack gap={compact ? "lg" : "xl"}>
        <Stack gap={6}>
          <Title
            order={2}
            ff="var(--font-serif)"
            fw={500}
            fz={compact ? 26 : 32}
            c="var(--mantine-color-text)"
          >
            {scoped ? "Progress in this source" : "Your progress"}
          </Title>
          <Text c="dimmed" maw={480}>
            First-try accuracy from graded answers
            {data.first_attempt_only ? " — Learn retries don’t recount" : ""}. Tutor questions you
            asked sit beside them.
          </Text>
        </Stack>

        <Paper withBorder radius="xl" p={compact ? "md" : "lg"} bg="gray.0" shadow="paper">
          <Stack gap="lg">
            <Group gap="xl" wrap="wrap">
              <StatFigure
                value={data.answers.total}
                label="answered"
                color="lavender"
                icon={<IconCheck size={18} stroke={2.2} />}
              />
              <StatFigure
                value={data.answers.correct}
                label="correct first try"
                color="sage"
                icon={<IconCheck size={18} stroke={2.2} />}
              />
              <StatFigure
                value={data.answers.wrong}
                label="to revisit"
                color="terracotta"
                icon={<IconX size={18} stroke={2.2} />}
              />
              <StatFigure
                value={data.questions_asked}
                label="questions asked"
                color="lavender"
                icon={<IconMessageCircle size={18} stroke={1.8} />}
              />
              <Box style={{ marginLeft: "auto", textAlign: "right" }}>
                <Text
                  fz={compact ? 28 : 34}
                  fw={600}
                  lh={1}
                  c="var(--mantine-color-text)"
                  style={{ fontFamily: "var(--font-serif), Georgia, serif" }}
                >
                  {accuracy == null ? "—" : `${accuracy}%`}
                </Text>
                <Text fz="xs" c="dimmed" mt={4}>
                  first-try accuracy
                </Text>
              </Box>
            </Group>

            {hasToday ? (
              <Text fz="sm" c="dimmed">
                Today: {data.today.answered} answered
                {data.today.answered > 0 ? ` (${data.today.correct} correct first try)` : ""}
                {data.today.questions_asked > 0 ? ` · ${data.today.questions_asked} asked` : ""}
              </Text>
            ) : null}
          </Stack>
        </Paper>

        <RecentActivity recent={data.recent} />
        <TopicCoverage topics={data.topics} onStudyConcept={scoped ? onStudyConcept : undefined} />
        {!scoped ? <SourceLedger sources={data.sources} highlightId={artifactId} /> : null}

        {scoped ? (
          <Group>
            {onStartLearn ? (
              <Button color="lavender" radius="xl" leftSection={<IconBulb size={16} />} onClick={onStartLearn}>
                Continue Learn
              </Button>
            ) : null}
            <Button
              component={Link}
              href="/workspace/progress"
              variant="subtle"
              color="gray"
              radius="xl"
              size="compact-sm"
            >
              See all sources
            </Button>
          </Group>
        ) : null}
      </Stack>
    </Box>
  );
}
