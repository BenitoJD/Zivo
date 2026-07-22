"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Box,
  Button,
  Center,
  FileButton,
  Group,
  Paper,
  Progress,
  RingProgress,
  SegmentedControl,
  Stack,
  Switch,
  Tabs,
  Text,
  Textarea,
  ThemeIcon,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconArrowRight,
  IconCheck,
  IconClock,
  IconKeyboard,
  IconPhoto,
  IconRefresh,
  IconWriting,
  IconX,
} from "@tabler/icons-react";
import { ensureGuestSession, apiUploadFile } from "@/lib/api/client";
import {
  useMainsQuery,
  useMainsActions,
  type MainsResult,
  type MainsStrictness,
} from "@/lib/api/queries";
import { WaitState } from "./WaitState";

const STRICTNESS: { value: MainsStrictness; label: string; blurb: string }[] = [
  { value: "exam", label: "Exam", blurb: "Hard marker, no benefit of the doubt." },
  { value: "coaching", label: "Coaching", blurb: "Fair but firm, names the biggest fix." },
  { value: "gentle", label: "Gentle", blurb: "Generous, leads with what worked." },
];

const bandColor = (pct: number) => (pct >= 70 ? "sage" : pct >= 55 ? "lavender" : pct >= 40 ? "yellow" : "terracotta");
const HL_COLOR: Record<string, string> = { strong: "sage", weak: "yellow", error: "terracotta" };

function fmt(total: number): string {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** The gradient-ring wait card (matches the generation wait), for generating/grading. */
function MainsWait({ title, detail, pct, compact }: { title: string; detail: string; pct: number; compact: boolean }) {
  const RING = compact ? 124 : 140;
  const R = RING / 2 - 12;
  const CIRC = 2 * Math.PI * R;
  const c = RING / 2;
  return (
    <Center h="100%" px="sm" py={compact ? "md" : "lg"}>
      <style>{`
        @keyframes mns-blob-a { 0%,100% { transform: translate(0,0) scale(1);} 50% { transform: translate(9px,-11px) scale(1.16);} }
        @keyframes mns-blob-b { 0%,100% { transform: translate(0,0) scale(1.06);} 50% { transform: translate(-11px,9px) scale(0.9);} }
        @keyframes mns-fade { from { opacity:0; transform: translateY(6px);} to { opacity:1; transform:none;} }
        .mns-copy { animation: mns-fade 380ms cubic-bezier(0.32,0.72,0,1) both; }
        @media (prefers-reduced-motion: reduce) { .mns-blob, .mns-copy { animation: none !important; } }
      `}</style>
      <Paper withBorder radius="xl" p={compact ? "lg" : "xl"} w="100%" maw={compact ? 380 : 440} style={{ background: "var(--mantine-color-body)" }}>
        <Stack align="center" gap={compact ? "md" : "lg"}>
          <Box pos="relative" w={RING} h={RING} style={{ display: "grid", placeItems: "center" }}>
            <Box className="mns-blob" pos="absolute" style={{ inset: -6, borderRadius: "50%", filter: "blur(22px)", background: "radial-gradient(60% 60% at 30% 30%, var(--mantine-color-lavender-4), transparent 70%)", opacity: 0.55, animation: "mns-blob-a 4.5s ease-in-out infinite" }} />
            <Box className="mns-blob" pos="absolute" style={{ inset: -6, borderRadius: "50%", filter: "blur(22px)", background: "radial-gradient(55% 55% at 72% 42%, var(--mantine-color-blue-4), transparent 70%)", opacity: 0.5, animation: "mns-blob-b 5.4s ease-in-out infinite" }} />
            <svg width={RING} height={RING} viewBox={`0 0 ${RING} ${RING}`} style={{ position: "relative" }}>
              <defs>
                <linearGradient id="mns-ring-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="var(--mantine-color-lavender-5)" />
                  <stop offset="50%" stopColor="var(--mantine-color-blue-5)" />
                  <stop offset="100%" stopColor="var(--mantine-color-sage-5)" />
                </linearGradient>
              </defs>
              <circle cx={c} cy={c} r={R} fill="none" stroke="var(--mantine-color-default-border)" strokeOpacity={0.5} strokeWidth={8} />
              <circle
                cx={c} cy={c} r={R} fill="none"
                stroke="url(#mns-ring-grad)" strokeWidth={8} strokeLinecap="round"
                strokeDasharray={CIRC} strokeDashoffset={CIRC * (1 - pct / 100)}
                transform={`rotate(-90 ${c} ${c})`}
                style={{ transition: "stroke-dashoffset 600ms cubic-bezier(0.32,0.72,0,1)" }}
              />
            </svg>
            <Box pos="absolute" style={{ display: "grid", placeItems: "center" }}>
              <Text fz={compact ? 22 : 26} fw={600} c="var(--mantine-color-text)" style={{ fontFamily: "var(--font-serif), Georgia, serif", lineHeight: 1 }}>
                {pct}%
              </Text>
            </Box>
          </Box>
          <Stack className="mns-copy" gap={4} align="center">
            <Text fz={compact ? "md" : "lg"} fw={600} ta="center" style={{ fontFamily: "var(--font-serif), Georgia, serif", letterSpacing: "-0.02em" }}>
              {title}
            </Text>
            <Text size="sm" c="dimmed" ta="center" lh={1.55} maw={290}>
              {detail}
            </Text>
          </Stack>
        </Stack>
      </Paper>
    </Center>
  );
}

function AxisMeter({ score, max }: { score: number; max: number }) {
  return (
    <Group gap={4} wrap="nowrap">
      {Array.from({ length: max }).map((_, i) => (
        <Box
          key={i}
          w={8}
          h={8}
          style={{
            borderRadius: "50%",
            background: i < score ? "var(--mantine-color-lavender-6)" : "var(--mantine-color-default-border)",
          }}
        />
      ))}
    </Group>
  );
}

function ResultView({
  result,
  strictness,
  onRestart,
  onRevise,
  busy,
  compact,
}: {
  result: MainsResult;
  strictness: MainsStrictness;
  onRestart: () => void;
  onRevise: () => void;
  busy: boolean;
  compact: boolean;
}) {
  const pct = result.marks_max ? Math.round((result.marks / result.marks_max) * 100) : 0;
  const color = bandColor(pct);
  return (
    <Stack gap="lg" pb="xl" align="center">
      <RingProgress
        size={compact ? 150 : 176}
        thickness={12}
        roundCaps
        sections={[{ value: pct, color }]}
        label={
          <Stack gap={0} align="center">
            <Text ta="center" ff="var(--font-serif)" fz={compact ? 30 : 38} fw={600} lh={1}>
              {result.marks}
              <Text span c="dimmed" fz={compact ? 16 : 20} fw={500}>
                /{result.marks_max}
              </Text>
            </Text>
            <Badge variant="light" color={color} radius="sm" mt={4}>
              {result.band}
            </Badge>
          </Stack>
        }
      />
      <Text c="dimmed" fz="sm" mt={-8} ta="center" maw={520}>
        {result.examiner_note}
      </Text>

      {/* per-axis scorecard */}
      <Stack gap="xs" w="100%" maw={560}>
        {result.axes.map((a) => (
          <Paper key={a.key} radius="lg" p="sm" withBorder style={{ borderColor: "var(--mantine-color-default-border)" }}>
            <Group justify="space-between" wrap="nowrap" mb={a.comment ? 4 : 0}>
              <Text fw={600} fz="sm">{a.label}</Text>
              <AxisMeter score={a.score} max={a.max} />
            </Group>
            {a.comment ? <Text c="dimmed" fz="xs" lh={1.5}>{a.comment}</Text> : null}
          </Paper>
        ))}
      </Stack>

      {/* keep doing / improve */}
      {(result.keep_doing.length || result.improve.length) ? (
        <Group gap="xl" w="100%" maw={560} align="flex-start" wrap="wrap">
          {result.keep_doing.length ? (
            <Box style={{ flex: 1, minWidth: 200 }}>
              <Text fz="xs" fw={700} tt="uppercase" c="sage.7" mb={6} style={{ letterSpacing: 0.4 }}>Keep doing</Text>
              {result.keep_doing.map((s, i) => <Text key={i} fz="sm" lh={1.5}>• {s}</Text>)}
            </Box>
          ) : null}
          {result.improve.length ? (
            <Box style={{ flex: 1, minWidth: 200 }}>
              <Text fz="xs" fw={700} tt="uppercase" c="terracotta.7" mb={6} style={{ letterSpacing: 0.4 }}>Improve</Text>
              {result.improve.map((s, i) => <Text key={i} fz="sm" lh={1.5}>• {s}</Text>)}
            </Box>
          ) : null}
        </Group>
      ) : null}

      {/* highlights — quoted phrases from the answer */}
      {result.highlights.length ? (
        <Stack gap={6} w="100%" maw={560}>
          <Text fz="xs" fw={700} tt="uppercase" c="dimmed" style={{ letterSpacing: 0.4 }}>On your answer</Text>
          {result.highlights.map((h, i) => (
            <Paper key={i} radius="md" p="sm" withBorder style={{ borderColor: `var(--mantine-color-${HL_COLOR[h.kind] ?? "gray"}-3)` }}>
              <Text fz="sm" style={{ fontStyle: "italic" }} c={`${HL_COLOR[h.kind] ?? "gray"}.7`}>“{h.quote}”</Text>
              {h.comment ? <Text fz="xs" c="dimmed" mt={2}>{h.comment}</Text> : null}
            </Paper>
          ))}
        </Stack>
      ) : null}

      {/* revealed marking scheme */}
      {result.scheme_hits.length ? (
        <Paper radius="lg" p="md" withBorder w="100%" maw={560} style={{ borderColor: "var(--mantine-color-default-border)" }}>
          <Text fw={600} fz="sm" mb={8}>What a full-marks answer needed</Text>
          <Stack gap={6}>
            {result.scheme_hits.map((h, i) => (
              <Group key={i} gap="xs" wrap="nowrap" align="flex-start">
                <ThemeIcon size={18} radius="xl" variant="light" color={h.hit ? "sage" : "terracotta"} mt={1}>
                  {h.hit ? <IconCheck size={12} stroke={3} /> : <IconX size={12} stroke={3} />}
                </ThemeIcon>
                <Text fz="sm" lh={1.45} style={{ flex: 1 }}>{h.point}</Text>
                <Text fz="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>{h.marks}m</Text>
              </Group>
            ))}
          </Stack>
        </Paper>
      ) : null}

      <Group gap="sm" mt={2}>
        <Button variant="default" radius="xl" leftSection={<IconArrowLeft size={16} />} disabled={busy} onClick={onRevise}>
          Revise answer
        </Button>
        <Button variant="light" color="blue" radius="xl" leftSection={<IconRefresh size={16} />} loading={busy} onClick={onRestart}>
          New question ({strictness})
        </Button>
      </Group>
    </Stack>
  );
}

export function MainsView({ artifactId, compact = false }: { artifactId: string; compact?: boolean }) {
  const { data, isError, refetch } = useMainsQuery(artifactId);
  const { start, answer } = useMainsActions(artifactId);

  const [strictness, setStrictness] = useState<MainsStrictness>("coaching");
  const [marksMax, setMarksMax] = useState(10);
  const [timerOn, setTimerOn] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [revising, setRevising] = useState(false); // "go back" to re-answer the same question
  const [tab, setTab] = useState<string | null>("type"); // which answer input is active
  const [busy, setBusy] = useState(false);

  const [typed, setTyped] = useState("");
  const [imageId, setImageId] = useState<string | null>(null);
  const [imageName, setImageName] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  // sync strictness picker with whatever the current attempt used
  useEffect(() => {
    if (data?.strictness) setStrictness(data.strictness);
  }, [data?.strictness]);

  // optional timer (deadline-based so a throttled tab still lands right)
  const [deadline, setDeadline] = useState<number | null>(null);
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    if (data?.status === "awaiting_answer" && timerOn && deadline === null) {
      setDeadline(Date.now() + (data.marks_max || 10) * 60 * 1000);
    }
    if (data?.status !== "awaiting_answer" && deadline !== null) setDeadline(null);
  }, [data?.status, data?.marks_max, timerOn, deadline]);
  useEffect(() => {
    if (deadline === null) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [deadline]);
  const secsLeft = deadline !== null ? Math.max(0, Math.round((deadline - now) / 1000)) : null;

  const fileReset = useRef<() => void>(() => {});

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      notifications.show({ title: "Something went wrong", message: e instanceof Error ? e.message : "Try again.", color: "terracotta" });
    } finally {
      setBusy(false);
    }
  }

  const doStart = () =>
    run(async () => {
      await start(strictness, marksMax);
      setRestarting(false);
      setRevising(false);
      setTyped("");
      setImageId(null);
      setImageName(null);
    });

  const doSubmit = () =>
    run(async () => {
      // Submit whichever input the ACTIVE tab holds — never let a stray photo override
      // typed work (or vice-versa) just because both fields happen to be populated.
      await answer(tab === "photo" ? { image_document_id: imageId! } : { text: typed.trim() });
      setRevising(false); // grading → result (not back into the answer screen)
    });

  async function handlePhoto(file: File | null) {
    if (!file) return;
    setUploading(true);
    try {
      await ensureGuestSession();
      const doc = await apiUploadFile<{ id: string }>(file);
      setImageId(doc.id);
      setImageName(file.name);
    } catch (e) {
      notifications.show({ title: "Upload failed", message: e instanceof Error ? e.message : "Could not upload photo.", color: "terracotta" });
    } finally {
      setUploading(false);
    }
  }

  const heading = useMemo(
    () => (
      <Group gap={8}>
        <ThemeIcon variant="light" color="blue" radius="xl" size="md"><IconWriting size={16} /></ThemeIcon>
        <Text ff="var(--font-serif)" fz={compact ? 20 : 26} fw={500}>Mains practice</Text>
      </Group>
    ),
    [compact],
  );

  // ---- gates ----
  if (isError) {
    return (
      <WaitState
        icon={<IconAlertTriangle size={26} />}
        title="Couldn't load Mains"
        body="We couldn't reach the grader. Check your connection and try again."
        action={<Button variant="light" color="blue" radius="xl" onClick={() => void refetch()}>Try again</Button>}
      />
    );
  }
  const failed = data?.status === "failed";
  if (!data || data.status === "indexing") {
    return <WaitState pet title="Getting your source ready" body="Indexing so we can set you a question…" />;
  }
  if (data.status === "generating") {
    return <MainsWait title="Setting your question" detail="Reading your source and writing a question with a hidden marking scheme." pct={45} compact={compact} />;
  }
  if (data.status === "grading") {
    return <MainsWait title="Marking your answer" detail="Reading it, checking it against the scheme, and writing examiner comments." pct={72} compact={compact} />;
  }
  if (data.status === "ready" && data.result && !restarting && !revising) {
    return (
      <ResultView
        result={data.result}
        strictness={strictness}
        busy={busy}
        compact={compact}
        onRestart={() => setRestarting(true)}
        onRevise={() => {
          setTyped(data.answer ?? "");
          setImageId(null);
          setImageName(null);
          setRevising(true);
        }}
      />
    );
  }

  // ---- setup screen (missing, failed, or "new question") ----
  if (data.status === "missing" || failed || restarting) {
    return (
      <Stack gap="lg" pb="xl">
        {heading}
        {failed ? (
          <Alert color="terracotta" variant="light" icon={<IconAlertTriangle size={16} />}>
            {data.error === "generation_failed"
              ? "We couldn't set a question from this source — try again, or pick a different source."
              : "Grading didn't finish. Set a fresh question and try again."}
          </Alert>
        ) : null}
        <Text c="dimmed" fz="sm" maw={520}>
          Zivo sets you an exam-style descriptive question from this source, you write a full answer (typed or a photo of
          your handwriting), and you get it back marked like an examiner — with a score, per-axis feedback, and the
          marking scheme revealed.
        </Text>

        <Paper radius="lg" p="md" withBorder style={{ borderColor: "var(--mantine-color-default-border)" }}>
          <Stack gap="md">
            <Group justify="space-between" wrap="nowrap">
              <Box><Text fw={600} fz="sm">Marks</Text><Text c="dimmed" fz="xs">Answer length scales with this.</Text></Box>
              <SegmentedControl
                size="xs"
                value={String(marksMax)}
                onChange={(v) => setMarksMax(Number(v))}
                data={[{ value: "10", label: "10 · ~150w" }, { value: "15", label: "15 · ~250w" }]}
              />
            </Group>
            <Group justify="space-between" wrap="nowrap" align="flex-start">
              <Box style={{ flex: 1 }}>
                <Text fw={600} fz="sm">Strictness</Text>
                <Text c="dimmed" fz="xs">{STRICTNESS.find((s) => s.value === strictness)?.blurb}</Text>
              </Box>
              <SegmentedControl
                size="xs"
                value={strictness}
                onChange={(v) => setStrictness(v as MainsStrictness)}
                data={STRICTNESS.map((s) => ({ value: s.value, label: s.label }))}
              />
            </Group>
            <Group justify="space-between" wrap="nowrap">
              <Box><Text fw={600} fz="sm">Practice under time</Text><Text c="dimmed" fz="xs">~1 minute per mark, examiner-style.</Text></Box>
              <Switch checked={timerOn} onChange={(e) => setTimerOn(e.currentTarget.checked)} color="blue" />
            </Group>
          </Stack>
        </Paper>

        <Group justify="flex-end">
          <Button color="blue" radius="xl" rightSection={<IconArrowRight size={16} />} loading={busy} onClick={doStart}>
            Set my question
          </Button>
        </Group>
      </Stack>
    );
  }

  // ---- awaiting_answer (or "revise") : the question + answer input ----
  const canSubmit = tab === "photo" ? imageId !== null : typed.trim().length > 0;
  return (
    <Stack gap="lg" pb="xl">
      <Group justify="space-between" wrap="nowrap">
        {revising ? (
          <Button variant="subtle" color="gray" size="xs" leftSection={<IconArrowLeft size={14} />} onClick={() => setRevising(false)}>
            Back to results
          </Button>
        ) : (
          heading
        )}
        {secsLeft !== null ? (
          <Badge size="lg" variant="light" color={secsLeft === 0 ? "terracotta" : "blue"} leftSection={<IconClock size={14} />}>
            {secsLeft === 0 ? "Time's up" : fmt(secsLeft)}
          </Badge>
        ) : null}
      </Group>

      <Paper radius="lg" p={compact ? "md" : "lg"} withBorder style={{ borderColor: "var(--mantine-color-default-border)" }}>
        <Group gap="xs" mb="sm">
          {data.directive ? <Badge variant="light" color="blue" radius="sm">{data.directive}</Badge> : null}
          <Badge variant="light" color="gray" radius="sm">{data.marks_max} marks</Badge>
        </Group>
        <Text ff="var(--font-serif)" fz={compact ? 18 : 22} fw={500} lh={1.5} style={{ whiteSpace: "pre-wrap" }}>
          {data.question}
        </Text>
      </Paper>

      <Tabs value={tab} onChange={setTab} variant="pills" color="blue">
        <Tabs.List mb="sm">
          <Tabs.Tab value="type" leftSection={<IconKeyboard size={15} />}>Type</Tabs.Tab>
          <Tabs.Tab value="photo" leftSection={<IconPhoto size={15} />}>Upload photo</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="type">
          <Textarea
            value={typed}
            onChange={(e) => setTyped(e.currentTarget.value)}
            placeholder="Write your full answer — introduction, body with dimensions and examples, then a conclusion with a verdict."
            autosize
            minRows={7}
            radius="md"
          />
        </Tabs.Panel>

        <Tabs.Panel value="photo">
          <Paper radius="md" p="lg" withBorder style={{ borderStyle: "dashed", borderColor: "var(--mantine-color-default-border)" }}>
            <Stack align="center" gap="xs">
              {imageId ? (
                <>
                  <ThemeIcon variant="light" color="sage" radius="xl" size={44}><IconCheck size={22} /></ThemeIcon>
                  <Text fz="sm" fw={500}>{imageName}</Text>
                  <Text fz="xs" c="dimmed">The vision reader will transcribe it when you submit.</Text>
                  <Button variant="subtle" color="gray" size="xs" onClick={() => { setImageId(null); setImageName(null); fileReset.current?.(); }}>
                    Choose a different photo
                  </Button>
                </>
              ) : (
                <>
                  <ThemeIcon variant="light" color="blue" radius="xl" size={44}><IconPhoto size={22} /></ThemeIcon>
                  <Text fz="sm" c="dimmed" ta="center">Upload a clear photo of your handwritten answer.</Text>
                  <FileButton onChange={handlePhoto} accept="image/*" resetRef={fileReset}>
                    {(props) => <Button {...props} variant="light" color="blue" radius="xl" loading={uploading}>Choose photo</Button>}
                  </FileButton>
                </>
              )}
            </Stack>
          </Paper>
        </Tabs.Panel>
      </Tabs>

      <Group justify="space-between">
        <Button variant="subtle" color="gray" radius="xl" onClick={() => setRestarting(true)} disabled={busy}>
          New question
        </Button>
        <Button color="blue" radius="xl" rightSection={<IconArrowRight size={16} />} disabled={!canSubmit} loading={busy} onClick={doSubmit}>
          {revising ? "Resubmit for marking" : "Submit for marking"}
        </Button>
      </Group>
    </Stack>
  );
}
