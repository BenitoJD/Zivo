"use client";

/**
 * System Design studio — continuous design → mentor report → teach-gap → next.
 */

import { useEffect, useState } from "react";
import {
  Accordion,
  Badge,
  Box,
  Button,
  Chip,
  Container,
  Group,
  Paper,
  Stack,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { IconArrowLeft, IconArrowRight, IconCheck } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { apiGet, ensureGuestSession } from "@/lib/api/client";
import {
  useSystemDesignActions,
  useSystemDesignProblemQuery,
  useSystemDesignSessionQuery,
  type SdDesign,
  type SdDimension,
  type SdSession,
} from "@/lib/api/queries";

const EMPTY_DESIGN: SdDesign = {
  requirements: "",
  apis: "",
  data: "",
  scale: "",
  blocks: [],
};

const SECTION_META: { key: keyof Omit<SdDesign, "blocks">; label: string; placeholder: string }[] = [
  {
    key: "requirements",
    label: "Requirements & constraints",
    placeholder: "What must it do? Scale? What will you not build?",
  },
  {
    key: "apis",
    label: "APIs & boundaries",
    placeholder: "Key endpoints, clients, service boundaries…",
  },
  {
    key: "data",
    label: "Data & consistency",
    placeholder: "Stores, ownership, consistency model…",
  },
  {
    key: "scale",
    label: "Scale, cache, failure",
    placeholder: "Hot path, caching, queues, failure modes…",
  },
];

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <Box
      bg="var(--mantine-color-body)"
      style={{ height: "100dvh", overflowY: "auto", overflowX: "hidden" }}
    >
      {children}
    </Box>
  );
}

function useResolvedParam(params: Promise<{ id: string }>): string | null {
  const [id, setId] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    void params.then((p) => {
      if (alive) setId(p.id);
    });
    return () => {
      alive = false;
    };
  }, [params]);
  return id;
}

function designFromSession(sess: SdSession | undefined): SdDesign {
  const d = sess?.design ?? {};
  return {
    requirements: String(d.requirements ?? ""),
    apis: String(d.apis ?? ""),
    data: String(d.data ?? ""),
    scale: String(d.scale ?? ""),
    blocks: Array.isArray(d.blocks) ? d.blocks.map(String) : [],
  };
}

export default function SystemDesignStudioPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const router = useRouter();
  const problemId = useResolvedParam(params);
  const actions = useSystemDesignActions();

  const problemQ = useSystemDesignProblemQuery(problemId, Boolean(problemId));
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [design, setDesign] = useState<SdDesign>(EMPTY_DESIGN);
  const [bootError, setBootError] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const sessionQ = useSystemDesignSessionQuery(sessionId, Boolean(sessionId));
  const session = sessionQ.data;
  const done = session?.status === "done";
  const blocks = session?.building_blocks?.length
    ? session.building_blocks
    : [
        "Client",
        "API / Gateway",
        "Load balancer",
        "App servers",
        "Cache",
        "Database",
        "Queue",
        "Object storage",
        "CDN",
        "Search index",
        "Workers",
      ];

  useEffect(() => {
    void ensureGuestSession();
  }, []);

  // Resume active session for this problem, else start fresh.
  useEffect(() => {
    if (!problemId) return;
    let cancelled = false;
    (async () => {
      setBooting(true);
      setBootError(null);
      try {
        await ensureGuestSession();
        const activeWrap = await apiGet<{ session: SdSession | null }>(
          "/api/system-design/sessions/active",
        );
        let sess: SdSession;
        if (activeWrap.session?.problem_id === problemId && activeWrap.session.status === "active") {
          sess = activeWrap.session;
        } else {
          sess = await actions.startSession(problemId);
        }
        if (cancelled) return;
        setSessionId(sess.id);
        setDesign(designFromSession(sess));
      } catch (e) {
        if (!cancelled) setBootError(e instanceof Error ? e.message : "Could not start session");
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- boot once per problem id
  }, [problemId]);

  const updateField = (key: keyof Omit<SdDesign, "blocks">, value: string) => {
    setDesign((d) => ({ ...d, [key]: value }));
  };

  const onSave = async () => {
    if (!sessionId || done) return;
    setSaving(true);
    try {
      await actions.saveDesign(sessionId, design);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const onSubmit = async () => {
    if (!sessionId || done) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await actions.submit(sessionId, design);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Submit failed");
    } finally {
      setSubmitting(false);
    }
  };

  const problem = session?.problem || problemQ.data;
  const mentorSummary = session?.feedback?.mentor_summary;
  const dimensions = session?.scores?.dimensions ?? [];
  const lesson = session?.lesson;
  const reference = session?.reference_design || problem?.reference_design || "";

  if (!problemId) {
    return (
      <Shell>
        <Container size="md" py="xl">
          <Text c="dimmed">No case id.</Text>
        </Container>
      </Shell>
    );
  }

  if (booting || problemQ.isLoading) {
    return (
      <Shell>
        <Container size="md" py="xl">
          <Text c="dimmed">Opening studio…</Text>
        </Container>
      </Shell>
    );
  }

  if (bootError || problemQ.isError || !problem) {
    return (
      <Shell>
        <Container size="md" py="xl">
          <Stack gap="md">
            <Text c="terracotta.7">{bootError || "This case couldn&rsquo;t be loaded."}</Text>
            <Button
              variant="light"
              leftSection={<IconArrowLeft size={16} />}
              onClick={() => router.push("/practice/system-design")}
            >
              Back to System Design
            </Button>
          </Stack>
        </Container>
      </Shell>
    );
  }

  return (
    <Shell>
      <Container size="sm" py={{ base: 16, md: 32 }}>
        <Button
          variant="subtle"
          size="xs"
          leftSection={<IconArrowLeft size={14} />}
          onClick={() => router.push("/practice/system-design")}
          mb="sm"
        >
          Path
        </Button>

        <Stack gap="lg">
          <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
            <Group justify="space-between" align="flex-start" wrap="wrap" gap="xs" mb="sm">
              <Title order={2} ff="var(--font-serif)" fw={500} lh={1.25}>
                {problem.title}
              </Title>
              <Badge
                variant="light"
                color={
                  problem.difficulty === "easy"
                    ? "sage"
                    : problem.difficulty === "hard"
                      ? "terracotta"
                      : "lavender"
                }
                radius="sm"
                tt="capitalize"
              >
                {problem.difficulty}
              </Badge>
            </Group>
            <Text fz="sm" lh={1.65} mb="sm">
              {problem.prompt}
            </Text>
            {problem.constraints ? (
              <Text fz="xs" c="dimmed">
                {problem.constraints}
              </Text>
            ) : null}
          </Paper>

          {!done ? (
            <Stack gap="md">
              {SECTION_META.map((s) => (
                <Box key={s.key}>
                  <Text fw={600} fz="sm" mb={6} ff="var(--font-serif)">
                    {s.label}
                  </Text>
                  <Textarea
                    value={design[s.key]}
                    onChange={(e) => updateField(s.key, e.currentTarget.value)}
                    placeholder={s.placeholder}
                    minRows={3}
                    autosize
                    maxRows={12}
                    radius="md"
                  />
                </Box>
              ))}

              <Box>
                <Text fw={600} fz="sm" mb={8} ff="var(--font-serif)">
                  Building blocks
                </Text>
                <Chip.Group
                  multiple
                  value={design.blocks}
                  onChange={(v) => setDesign((d) => ({ ...d, blocks: v }))}
                >
                  <Group gap={6}>
                    {blocks.map((b) => (
                      <Chip key={b} value={b} size="xs" radius="xl" variant="light" color="lavender">
                        {b}
                      </Chip>
                    ))}
                  </Group>
                </Chip.Group>
              </Box>

              {submitError ? (
                <Text c="terracotta" fz="sm">
                  {submitError}
                </Text>
              ) : null}

              <Group gap="sm">
                <Button radius="xl" variant="light" color="gray" loading={saving} onClick={() => void onSave()}>
                  Save draft
                </Button>
                <Button
                  radius="xl"
                  color="lavender"
                  loading={submitting}
                  rightSection={<IconCheck size={16} />}
                  onClick={() => void onSubmit()}
                >
                  Submit for mentor
                </Button>
              </Group>
            </Stack>
          ) : (
            <MentorReport
              mentorSummary={mentorSummary}
              dimensions={dimensions}
              weakConcepts={session?.weak_concepts ?? []}
              lesson={lesson}
              reference={reference}
              recommendedNextId={session?.recommended_next_id ?? null}
              onNext={(id) => router.push(`/practice/system-design/${id}`)}
              onPath={() => router.push("/practice/system-design")}
            />
          )}
        </Stack>
      </Container>
    </Shell>
  );
}

function MentorReport({
  mentorSummary,
  dimensions,
  weakConcepts,
  lesson,
  reference,
  recommendedNextId,
  onNext,
  onPath,
}: {
  mentorSummary?: string;
  dimensions: SdDimension[];
  weakConcepts: string[];
  lesson?: Partial<{ title: string; body: string; try_this: string }>;
  reference: string;
  recommendedNextId: string | null;
  onNext: (id: string) => void;
  onPath: () => void;
}) {
  return (
    <Stack gap="md">
      <Paper radius="xl" p="lg" withBorder bg="gray.0" shadow="paper">
        <Text size="xs" fw={600} tt="uppercase" lts={1.2} c="lavender.8" mb={8}>
          Mentor
        </Text>
        <Text ff="var(--font-serif)" fz="lg" fw={500} lh={1.45} mb="md">
          {mentorSummary || "Solid attempt — tighten the weakest dimension next."}
        </Text>
        <Stack gap={8}>
          {dimensions.map((d) => (
            <Group key={d.key} justify="space-between" wrap="nowrap" gap="sm">
              <Box style={{ minWidth: 0 }}>
                <Text fz="sm" fw={600} tt="capitalize">
                  {d.key}
                </Text>
                {d.note ? (
                  <Text fz="xs" c="dimmed" lineClamp={2}>
                    {d.note}
                  </Text>
                ) : null}
              </Box>
              <Badge
                variant="light"
                color={d.score >= 3 ? "sage" : d.score <= 1 ? "terracotta" : "lavender"}
                radius="sm"
              >
                {d.score}/4
              </Badge>
            </Group>
          ))}
        </Stack>
        {weakConcepts.length ? (
          <Group gap={6} mt="md">
            {weakConcepts.map((w) => (
              <Badge key={w} variant="light" color="gray" radius="sm" size="sm">
                {w}
              </Badge>
            ))}
          </Group>
        ) : null}
      </Paper>

      {(lesson?.title || lesson?.body) && (
        <Paper radius="xl" p="lg" withBorder bg="lavender.0" style={{ borderColor: "var(--mantine-color-lavender-2)" }}>
          <Text size="xs" fw={600} tt="uppercase" lts={1.2} c="lavender.8" mb={6}>
            Close the gap
          </Text>
          <Title order={4} ff="var(--font-serif)" fw={500} mb={6}>
            {lesson?.title || "Lesson"}
          </Title>
          {lesson?.body ? (
            <Text fz="sm" lh={1.65} mb="sm">
              {lesson.body}
            </Text>
          ) : null}
          {lesson?.try_this ? (
            <Text fz="sm" fs="italic" c="gray.7">
              Try this: {lesson.try_this}
            </Text>
          ) : null}
        </Paper>
      )}

      {reference ? (
        <Accordion variant="separated" radius="lg">
          <Accordion.Item value="reference">
            <Accordion.Control>
              <Text fw={500} fz="sm">
                Reference design
              </Text>
            </Accordion.Control>
            <Accordion.Panel>
              <Text fz="sm" lh={1.65} c="dimmed">
                {reference}
              </Text>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      ) : null}

      <Group gap="sm" wrap="wrap">
        {recommendedNextId ? (
          <Button
            radius="xl"
            color="lavender"
            rightSection={<IconArrowRight size={16} />}
            onClick={() => onNext(recommendedNextId)}
          >
            Practice the gap
          </Button>
        ) : null}
        <Button radius="xl" variant="subtle" color="gray" onClick={onPath}>
          Back to path
        </Button>
      </Group>
    </Stack>
  );
}
