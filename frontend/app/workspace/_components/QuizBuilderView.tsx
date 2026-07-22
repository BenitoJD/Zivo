"use client";

import { useState } from "react";
import {
  Badge,
  Box,
  Button,
  Checkbox,
  Group,
  NumberInput,
  Paper,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  ThemeIcon,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconFileTypeDocx,
  IconFileTypePdf,
  IconListCheck,
  IconMarkdown,
  IconFileText,
  IconSparkles,
} from "@tabler/icons-react";
import { apiFetchBytes } from "@/lib/api/client";
import { useQuizQuery, type QuizConfig, type QuizQuestion } from "@/lib/api/queries";
import { WaitState } from "./WaitState";

// MCQ-style variants - all single-best-answer, same payload as "mcq" (options +
// answer_index), so they render with the MCQ branch of QuestionCard.
const MCQ_STYLE_TYPES: { value: string; label: string }[] = [
  { value: "mcq", label: "Multiple choice" },
  { value: "mcq_negative", label: "Negative (EXCEPT)" },
  { value: "assertion_reason", label: "Assertion-Reason" },
  { value: "scenario", label: "Scenario" },
  { value: "cloze", label: "Fill-in (cloze)" },
];
const OTHER_TYPES: { value: string; label: string }[] = [
  { value: "multi", label: "Multiple answer" },
  { value: "truefalse", label: "True / False" },
  { value: "fill_blank", label: "Fill in the blank" },
  { value: "short", label: "Short answer" },
  { value: "essay", label: "Essay" },
  { value: "matching", label: "Matching" },
];
const TYPES: { value: string; label: string }[] = [...MCQ_STYLE_TYPES, ...OTHER_TYPES];
// Single-best-answer MCQ variants share the "mcq" payload + rendering.
const SINGLE_ANSWER_MCQ = new Set(["mcq", "mcq_negative", "assertion_reason", "scenario", "cloze"]);
const LETTERS = "ABCDEFGH";

/**
 * Question Generator - turn any source into a worksheet/quiz. Pick the question types,
 * count and difficulty; the AI writes a question set with an answer key, previewable as
 * a teacher (answers) or student version and exportable to Word / PDF / Markdown / text.
 */
export function QuizBuilderView({ artifactId, compact = false }: { artifactId: string; compact?: boolean }) {
  const [draftTypes, setDraftTypes] = useState<string[]>(["mcq", "truefalse", "short"]);
  const [count, setCount] = useState(10);
  const [difficulty, setDifficulty] = useState("mixed");
  const [applied, setApplied] = useState<QuizConfig | null>(null);
  const [showAnswers, setShowAnswers] = useState(true);
  const [downloading, setDownloading] = useState<string | null>(null);

  const { data, isError, refetch } = useQuizQuery(artifactId, applied ?? { types: [], count, difficulty }, Boolean(applied));
  const status = data?.status;
  const questions = data?.questions ?? [];
  const ready = Boolean(applied) && status === "ready" && questions.length > 0;
  const building = Boolean(applied) && !ready && status !== "failed" && !isError;

  function generate() {
    if (draftTypes.length === 0) return;
    setApplied({ types: draftTypes, count, difficulty });
  }

  async function downloadDocx(withAnswers: boolean) {
    setDownloading(withAnswers ? "docx-a" : "docx");
    try {
      const buf = await apiFetchBytes(`/api/artifacts/${artifactId}/quiz/export.docx?answers=${withAnswers ? 1 : 0}`);
      triggerDownload(new Blob([buf]), `quiz-${withAnswers ? "answer-key" : "worksheet"}.docx`);
    } catch {
      /* ignore */
    } finally {
      setDownloading(null);
    }
  }
  function downloadText(ext: "md" | "txt") {
    const body = ext === "md" ? toMarkdown(questions, showAnswers) : toPlainText(questions, showAnswers);
    triggerDownload(new Blob([body], { type: "text/plain" }), `quiz.${ext}`);
  }
  function printPdf() {
    const win = window.open("", "_blank", "width=820,height=1000");
    if (!win) return;
    win.document.write(
      `<!doctype html><html><head><meta charset="utf-8"><title>Quiz</title>` +
        `<style>body{font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:720px;margin:0 auto;padding:32px;color:#1a1a1a}` +
        `h1{font-size:20px}ol{padding-left:20px}li{margin:0 0 16px}.op{margin-left:8px}.ans{color:#5a4bd6;font-style:italic;margin-top:4px}@page{margin:16mm}</style>` +
        `</head><body>${toHtml(questions, showAnswers)}</body></html>`,
    );
    win.document.close();
    win.focus();
    setTimeout(() => win.print(), 500);
  }

  return (
    <Stack gap="lg" pb="xl">
      {/* Config */}
      <Paper radius="lg" p={compact ? "md" : "lg"} withBorder style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))" }}>
        <Group gap={8} mb="sm">
          <ThemeIcon variant="light" color="lavender" radius="xl" size="md"><IconListCheck size={16} /></ThemeIcon>
          <Text ff="var(--font-serif)" fz={compact ? 18 : 22} fw={500}>Generate a worksheet</Text>
        </Group>
        <Text c="dimmed" fz="sm" mb="md">Pick the question types, then generate a quiz from this source - with an answer key, ready to export.</Text>

        <Checkbox.Group value={draftTypes} onChange={setDraftTypes} label="Question types">
          <Text fz="xs" fw={600} c="lavender.6" tt="uppercase" mt="xs" mb={6} style={{ letterSpacing: 0.4 }}>
            MCQ styles
          </Text>
          <Group gap="sm" wrap="wrap">
            {MCQ_STYLE_TYPES.map((t) => (
              <Checkbox key={t.value} value={t.value} label={t.label} radius="sm" color="lavender" />
            ))}
          </Group>
          <Text fz="xs" fw={600} c="dimmed" tt="uppercase" mt="md" mb={6} style={{ letterSpacing: 0.4 }}>
            Other types
          </Text>
          <Group gap="sm" wrap="wrap">
            {OTHER_TYPES.map((t) => (
              <Checkbox key={t.value} value={t.value} label={t.label} radius="sm" color="lavender" />
            ))}
          </Group>
        </Checkbox.Group>

        <Group gap="xl" mt="md" wrap="wrap" align="flex-end">
          <NumberInput label="Questions" value={count} onChange={(v) => setCount(Math.max(1, Math.min(40, Number(v) || 10)))} min={1} max={40} w={120} radius="md" />
          <Box>
            <Text fz="sm" fw={500} mb={4}>Difficulty</Text>
            <SegmentedControl size="xs" radius="xl" value={difficulty} onChange={setDifficulty}
              data={[{ label: "Easy", value: "easy" }, { label: "Medium", value: "medium" }, { label: "Hard", value: "hard" }, { label: "Mixed", value: "mixed" }]} />
          </Box>
          <Button color="lavender" radius="xl" leftSection={<IconSparkles size={16} />} disabled={draftTypes.length === 0} loading={building} onClick={generate}>
            {applied ? "Regenerate" : "Generate quiz"}
          </Button>
        </Group>
      </Paper>

      {isError || status === "failed" ? (
        <WaitState icon={<IconAlertTriangle size={26} />} title="Couldn’t build the quiz" body="Something went wrong. Try again in a moment."
          action={<Button variant="light" color="lavender" radius="xl" onClick={() => void refetch()}>Try again</Button>} />
      ) : building ? (
        <WaitState pet title="Writing your questions" body="Reading the source and drafting an exam-quality question set…" />
      ) : ready ? (
        <>
          <Group justify="space-between" wrap="wrap" gap="sm">
            <Group gap={6}>
              <Switch checked={showAnswers} onChange={(e) => setShowAnswers(e.currentTarget.checked)} color="lavender" size="sm"
                label={showAnswers ? "Answer key (teacher)" : "Student version"} />
            </Group>
            <Group gap={6} wrap="wrap">
              <Button size="xs" variant="default" radius="xl" leftSection={<IconFileTypeDocx size={14} />} loading={downloading === "docx" || downloading === "docx-a"} onClick={() => void downloadDocx(showAnswers)}>Word</Button>
              <Button size="xs" variant="default" radius="xl" leftSection={<IconFileTypePdf size={14} />} onClick={printPdf}>PDF</Button>
              <Button size="xs" variant="default" radius="xl" leftSection={<IconMarkdown size={14} />} onClick={() => downloadText("md")}>Markdown</Button>
              <Button size="xs" variant="default" radius="xl" leftSection={<IconFileText size={14} />} onClick={() => downloadText("txt")}>Text</Button>
            </Group>
          </Group>
          <Stack gap="md">
            {questions.map((q, i) => (
              <QuestionCard key={i} n={i + 1} q={q} showAnswers={showAnswers} compact={compact} />
            ))}
          </Stack>
        </>
      ) : null}
    </Stack>
  );
}

function QuestionCard({ n, q, showAnswers, compact }: { n: number; q: QuizQuestion; showAnswers: boolean; compact?: boolean }) {
  const typeLabel = TYPES.find((t) => t.value === q.type)?.label ?? q.type;
  return (
    <Paper radius="lg" p={compact ? "sm" : "md"} withBorder style={{ borderColor: "var(--app-border, var(--mantine-color-gray-2))" }}>
      <Group gap={8} mb={6} align="center">
        <Text fw={700} c="lavender.6" fz="sm" w={22}>{n}</Text>
        <Badge size="xs" variant="light" color="gray" radius="sm">{typeLabel}</Badge>
      </Group>
      <Text fz={compact ? "sm" : "md"} fw={500} lh={1.5} mb="xs">{q.prompt}</Text>

      {(SINGLE_ANSWER_MCQ.has(q.type) || q.type === "multi") && q.options ? (
        <Stack gap={4} pl={4}>
          {q.options.map((opt, oi) => {
            const correct = q.type === "multi" ? (q.answer_indices ?? []).includes(oi) : q.answer_index === oi;
            return (
              <Group key={oi} gap={8} wrap="nowrap">
                <Text fz="sm" fw={600} c={showAnswers && correct ? "sage.7" : "dimmed"} w={18}>{LETTERS[oi]}</Text>
                <Text fz="sm" c={showAnswers && correct ? "sage.7" : "var(--mantine-color-text)"} fw={showAnswers && correct ? 600 : 400}>
                  {opt}{showAnswers && correct ? "  ✓" : ""}
                </Text>
              </Group>
            );
          })}
        </Stack>
      ) : null}

      {q.type === "truefalse" ? (
        <Text fz="sm" c="dimmed">True / False{showAnswers ? ` - Answer: ${q.answer ? "True" : "False"}` : ""}</Text>
      ) : null}

      {q.type === "matching" && q.pairs ? (
        <Group align="flex-start" gap="xl" wrap="nowrap">
          <Stack gap={2}>{q.pairs.map((p, pi) => <Text key={pi} fz="sm">{pi + 1}. {p.left}</Text>)}</Stack>
          <Stack gap={2}>{q.pairs.map((p, pi) => <Text key={pi} fz="sm">{LETTERS[pi]}. {p.right}</Text>)}</Stack>
        </Group>
      ) : null}

      {showAnswers && (q.type === "fill_blank" || q.type === "short" || q.type === "essay") ? (
        <Text fz="sm" c="sage.7" mt={4}>Answer: {String(q.answer ?? "")}</Text>
      ) : null}
      {showAnswers && q.type === "matching" ? (
        <Text fz="xs" c="sage.7" mt={6}>Key: {q.pairs?.map((_p, pi) => `${pi + 1}→${LETTERS[pi]}`).join("  ")}</Text>
      ) : null}
      {showAnswers && q.explanation ? <Text fz="xs" c="dimmed" fs="italic" mt={4}>{q.explanation}</Text> : null}
    </Paper>
  );
}

function answerLine(q: QuizQuestion): string {
  if (SINGLE_ANSWER_MCQ.has(q.type)) return `${LETTERS[q.answer_index ?? 0]}`;
  if (q.type === "multi") return (q.answer_indices ?? []).map((i) => LETTERS[i]).join(", ");
  if (q.type === "truefalse") return q.answer ? "True" : "False";
  if (q.type === "matching") return (q.pairs ?? []).map((_p, i) => `${i + 1}→${LETTERS[i]}`).join("  ");
  return String(q.answer ?? "");
}
function toMarkdown(qs: QuizQuestion[], withAnswers: boolean): string {
  const lines: string[] = ["# Quiz", ""];
  qs.forEach((q, i) => {
    lines.push(`**${i + 1}. ${q.prompt}**`);
    if (q.options) q.options.forEach((o, oi) => lines.push(`   - ${LETTERS[oi]}. ${o}`));
    if (q.type === "truefalse") lines.push("   - True / False");
    if (q.type === "matching") (q.pairs ?? []).forEach((p, pi) => lines.push(`   - ${pi + 1}. ${p.left} - ${LETTERS[pi]}. ${p.right}`));
    if (withAnswers) {
      lines.push(`   - _Answer:_ ${answerLine(q)}`);
      if (q.explanation) lines.push(`   - _${q.explanation}_`);
    }
    lines.push("");
  });
  return lines.join("\n");
}
function toPlainText(qs: QuizQuestion[], withAnswers: boolean): string {
  return qs
    .map((q, i) => {
      let s = `${i + 1}. ${q.prompt}`;
      if (q.options) s += "\n" + q.options.map((o, oi) => `   ${LETTERS[oi]}. ${o}`).join("\n");
      if (q.type === "truefalse") s += "\n   True / False";
      if (q.type === "matching") s += "\n" + (q.pairs ?? []).map((p, pi) => `   ${pi + 1}. ${p.left}    ${LETTERS[pi]}. ${p.right}`).join("\n");
      if (withAnswers) s += `\n   Answer: ${answerLine(q)}`;
      return s;
    })
    .join("\n\n");
}
function toHtml(qs: QuizQuestion[], withAnswers: boolean): string {
  const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c] as string));
  const items = qs
    .map((q) => {
      let inner = `<div><strong>${esc(q.prompt)}</strong></div>`;
      if (q.options) inner += q.options.map((o, oi) => `<div class="op">${LETTERS[oi]}. ${esc(o)}</div>`).join("");
      if (q.type === "truefalse") inner += `<div class="op">True / False</div>`;
      if (q.type === "matching") inner += (q.pairs ?? []).map((p, pi) => `<div class="op">${pi + 1}. ${esc(p.left)} - ${LETTERS[pi]}. ${esc(p.right)}</div>`).join("");
      if (withAnswers) inner += `<div class="ans">Answer: ${esc(answerLine(q))}${q.explanation ? " - " + esc(q.explanation) : ""}</div>`;
      return `<li>${inner}</li>`;
    })
    .join("");
  return `<h1>Quiz${withAnswers ? " - Answer key" : ""}</h1><ol>${items}</ol>`;
}
function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}