"use client";

import { useEffect, useState } from "react";
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Group,
  Paper,
  RingProgress,
  SegmentedControl,
  Stack,
  Text,
  Textarea,
  TextInput,
  ThemeIcon,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconCheck,
  IconDownload,
  IconPlus,
  IconSparkles,
  IconTrash,
  IconUserCheck,
  IconWand,
  IconX,
} from "@tabler/icons-react";
import { apiPostBytes } from "@/lib/api/client";
import {
  useResumeAtsQuery,
  useResumeActions,
  type OptimizeResult,
  type ResumeJob,
  type ResumeStructured,
} from "@/lib/api/queries";
import { WaitState } from "./WaitState";

const scoreColor = (pct: number) => (pct >= 67 ? "sage" : pct >= 40 ? "lavender" : "terracotta");
const border = "var(--app-border, var(--mantine-color-gray-2))";

/**
 * Resume suite (jobbie-style) - ATS score/checklist, AI optimizer, and a docx builder, all on
 * an uploaded resume. Reuses the resume the app already ingested; the AI extracts a structured
 * resume that pre-fills the builder.
 */
export function ResumeView({ artifactId, compact = false }: { artifactId: string; compact?: boolean }) {
  const [tab, setTab] = useState("score");
  return (
    <Stack gap="lg" pb="xl">
      <Group gap={8}>
        <ThemeIcon variant="light" color="lavender" radius="xl" size="md"><IconSparkles size={16} /></ThemeIcon>
        <Text ff="var(--font-serif)" fz={compact ? 20 : 26} fw={500}>Resume tools</Text>
      </Group>
      <SegmentedControl
        value={tab}
        onChange={setTab}
        radius="xl"
        data={[
          { label: "ATS Score", value: "score" },
          { label: "Optimize", value: "optimize" },
          { label: "Build", value: "build" },
        ]}
      />
      {tab === "score" ? <ScoreTab artifactId={artifactId} compact={compact} /> : null}
      {tab === "optimize" ? <OptimizeTab artifactId={artifactId} /> : null}
      {tab === "build" ? <BuildTab artifactId={artifactId} /> : null}
    </Stack>
  );
}

// ------------------------------------------------------------------ ATS score
function ScoreTab({ artifactId, compact }: { artifactId: string; compact?: boolean }) {
  const { data, isError, refetch } = useResumeAtsQuery(artifactId);
  const { requestReview } = useResumeActions(artifactId);
  const [requesting, setRequesting] = useState(false);

  if (isError || data?.status === "failed") {
    return (
      <WaitState icon={<IconAlertTriangle size={26} />} title="Couldn’t analyse the resume"
        body="Make sure the uploaded source is a resume, then try again."
        action={<Button variant="light" color="lavender" radius="xl" onClick={() => void refetch()}>Try again</Button>} />
    );
  }
  if (!data || data.status === "indexing" || data.status === "pending" || !data.analysis?.checks) {
    return <WaitState pet title="Scoring your resume" body="Running ATS checks and reading your content…" />;
  }
  const a = data.analysis;
  const score = a.score ?? 0;

  return (
    <Stack gap="lg">
      <Group align="center" gap="xl" wrap="wrap">
        <RingProgress
          size={compact ? 120 : 148}
          thickness={12}
          roundCaps
          sections={[{ value: score, color: scoreColor(score) }]}
          label={<Text ta="center" ff="var(--font-serif)" fz={compact ? 26 : 34} fw={600}>{score}</Text>}
        />
        <Box style={{ flex: 1, minWidth: 200 }}>
          <Text ff="var(--font-serif)" fz="lg" fw={500}>ATS readiness</Text>
          <Text c="dimmed" fz="sm">Format checks {a.det_score ?? 0}/100 · Content {a.content_score ?? 0}/100</Text>
          <Button mt="sm" size="xs" variant={data.review_requested ? "light" : "filled"} color={data.review_requested ? "sage" : "lavender"} radius="xl"
            leftSection={<IconUserCheck size={14} />} loading={requesting} disabled={data.review_requested}
            onClick={async () => { setRequesting(true); try { await requestReview(); } finally { setRequesting(false); } }}>
            {data.review_requested ? "Expert review requested" : "Request expert review"}
          </Button>
        </Box>
      </Group>

      <Stack gap={6}>
        {(a.checks ?? []).map((c) => (
          <Group key={c.name} gap={10} wrap="nowrap" align="flex-start">
            <ThemeIcon variant="light" color={c.pass ? "sage" : "terracotta"} radius="xl" size="sm" mt={2}>
              {c.pass ? <IconCheck size={12} /> : <IconX size={12} />}
            </ThemeIcon>
            <Box>
              <Text fz="sm" fw={500}>{c.name}</Text>
              <Text fz="xs" c="dimmed">{c.detail}</Text>
            </Box>
          </Group>
        ))}
      </Stack>

      {(a.strengths?.length || a.improvements?.length) ? (
        <Group align="flex-start" gap="xl" wrap="wrap">
          {a.strengths?.length ? (
            <Box style={{ flex: 1, minWidth: 220 }}>
              <Text fz="xs" fw={700} tt="uppercase" c="sage.7" mb={6}>Strengths</Text>
              {a.strengths.map((s, i) => <Text key={i} fz="sm">• {s}</Text>)}
            </Box>
          ) : null}
          {a.improvements?.length ? (
            <Box style={{ flex: 1, minWidth: 220 }}>
              <Text fz="xs" fw={700} tt="uppercase" c="terracotta.7" mb={6}>Fix next</Text>
              {a.improvements.map((s, i) => <Text key={i} fz="sm">• {s}</Text>)}
            </Box>
          ) : null}
        </Group>
      ) : null}
    </Stack>
  );
}

// ------------------------------------------------------------------ optimizer
function OptimizeTab({ artifactId }: { artifactId: string }) {
  const { optimize } = useResumeActions(artifactId);
  const [jd, setJd] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<OptimizeResult | null>(null);

  async function run() {
    setBusy(true);
    try { setResult(await optimize(jd)); } finally { setBusy(false); }
  }

  return (
    <Stack gap="md">
      <Text c="dimmed" fz="sm">Paste a target job description (optional) to tailor the rewrite, or leave it blank for a general ATS polish.</Text>
      <Textarea value={jd} onChange={(e) => setJd(e.currentTarget.value)} placeholder="Paste the job description here…" autosize minRows={4} radius="md" />
      <Group>
        <Button color="lavender" radius="xl" leftSection={<IconWand size={16} />} loading={busy} onClick={() => void run()}>
          Optimize my bullets
        </Button>
      </Group>

      {result ? (
        <Stack gap="md">
          {result.notes ? <Text fz="sm" c="dimmed" fs="italic">{result.notes}</Text> : null}
          {result.summary ? (
            <Paper radius="lg" p="md" withBorder style={{ borderColor: border }}>
              <Text fz="xs" fw={700} tt="uppercase" c="lavender.6" mb={4}>Suggested summary</Text>
              <Text fz="sm">{result.summary}</Text>
            </Paper>
          ) : null}
          {result.missing_keywords.length ? (
            <Box>
              <Text fz="xs" fw={700} tt="uppercase" c="terracotta.7" mb={6}>Missing keywords</Text>
              <Group gap={6} wrap="wrap">
                {result.missing_keywords.map((k, i) => <Badge key={i} variant="light" color="terracotta" radius="sm">{k}</Badge>)}
              </Group>
            </Box>
          ) : null}
          {result.bullets.map((b, i) => (
            <Paper key={i} radius="lg" p="md" withBorder style={{ borderColor: border }}>
              <Text fz="xs" c="dimmed" td="line-through" mb={4}>{b.original}</Text>
              <Text fz="sm" c="sage.7" fw={500}>{b.improved}</Text>
            </Paper>
          ))}
        </Stack>
      ) : null}
    </Stack>
  );
}

// ------------------------------------------------------------------ builder
const EMPTY: ResumeStructured = { name: "", title: "", email: "", phone: "", location: "", links: [], summary: "", experience: [], education: [], skills: [] };

function BuildTab({ artifactId }: { artifactId: string }) {
  const { data } = useResumeAtsQuery(artifactId);
  const [form, setForm] = useState<ResumeStructured>(EMPTY);
  const [template, setTemplate] = useState<"ats" | "modern">("ats");
  const [downloading, setDownloading] = useState(false);
  const structured = data?.analysis?.structured;

  // Pre-fill the builder from the AI-extracted resume once it's available.
  useEffect(() => {
    if (structured && Object.keys(structured).length) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- seed builder from the extracted resume
      setForm({ ...EMPTY, ...structured });
    }
  }, [structured]);

  const set = (k: keyof ResumeStructured, v: unknown) => setForm((f) => ({ ...f, [k]: v }));
  const setJob = (i: number, patch: Partial<ResumeJob>) =>
    setForm((f) => ({ ...f, experience: (f.experience ?? []).map((j, idx) => (idx === i ? { ...j, ...patch } : j)) }));

  async function download() {
    setDownloading(true);
    try {
      const buf = await apiPostBytes(`/api/artifacts/${artifactId}/resume/build.docx`, { data: form, template });
      const url = URL.createObjectURL(new Blob([buf]));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${(form.name || "resume").replace(/\s+/g, "-")}-${template}.docx`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Stack gap="md">
      <Text c="dimmed" fz="sm">Pre-filled from your uploaded resume - edit anything, pick a template, and download a clean .docx.</Text>
      <Group grow>
        <TextInput label="Name" value={form.name ?? ""} onChange={(e) => set("name", e.currentTarget.value)} radius="md" />
        <TextInput label="Title" value={form.title ?? ""} onChange={(e) => set("title", e.currentTarget.value)} radius="md" />
      </Group>
      <Group grow>
        <TextInput label="Email" value={form.email ?? ""} onChange={(e) => set("email", e.currentTarget.value)} radius="md" />
        <TextInput label="Phone" value={form.phone ?? ""} onChange={(e) => set("phone", e.currentTarget.value)} radius="md" />
        <TextInput label="Location" value={form.location ?? ""} onChange={(e) => set("location", e.currentTarget.value)} radius="md" />
      </Group>
      <Textarea label="Summary" value={form.summary ?? ""} onChange={(e) => set("summary", e.currentTarget.value)} autosize minRows={2} radius="md" />
      <TextInput label="Skills (comma-separated)" value={(form.skills ?? []).join(", ")}
        onChange={(e) => set("skills", e.currentTarget.value.split(",").map((s) => s.trim()).filter(Boolean))} radius="md" />

      <Group justify="space-between" align="center" mt="xs">
        <Text fz="sm" fw={600}>Experience</Text>
        <Button size="compact-xs" variant="light" color="lavender" radius="xl" leftSection={<IconPlus size={12} />}
          onClick={() => set("experience", [...(form.experience ?? []), { role: "", company: "", dates: "", bullets: [] }])}>
          Add role
        </Button>
      </Group>
      {(form.experience ?? []).map((job, i) => (
        <Paper key={i} radius="md" p="sm" withBorder style={{ borderColor: border }}>
          <Group justify="flex-end" mb={4}>
            <ActionIcon variant="subtle" color="terracotta" size="sm"
              onClick={() => set("experience", (form.experience ?? []).filter((_, idx) => idx !== i))}>
              <IconTrash size={14} />
            </ActionIcon>
          </Group>
          <Group grow>
            <TextInput placeholder="Role" value={job.role} onChange={(e) => setJob(i, { role: e.currentTarget.value })} radius="md" size="xs" />
            <TextInput placeholder="Company" value={job.company} onChange={(e) => setJob(i, { company: e.currentTarget.value })} radius="md" size="xs" />
            <TextInput placeholder="Dates" value={job.dates} onChange={(e) => setJob(i, { dates: e.currentTarget.value })} radius="md" size="xs" />
          </Group>
          <Textarea mt={6} placeholder="One bullet per line" value={(job.bullets ?? []).join("\n")}
            onChange={(e) => setJob(i, { bullets: e.currentTarget.value.split("\n").map((b) => b.trim()).filter(Boolean) })}
            autosize minRows={2} radius="md" size="xs" />
        </Paper>
      ))}

      <Group justify="space-between" align="flex-end" mt="md" wrap="wrap">
        <Box>
          <Text fz="sm" fw={500} mb={4}>Template</Text>
          <SegmentedControl size="xs" radius="xl" value={template} onChange={(v) => setTemplate(v as "ats" | "modern")}
            data={[{ label: "ATS (plain)", value: "ats" }, { label: "Modern", value: "modern" }]} />
        </Box>
        <Button color="lavender" radius="xl" leftSection={<IconDownload size={16} />} loading={downloading} onClick={() => void download()}>
          Download .docx
        </Button>
      </Group>
    </Stack>
  );
}
