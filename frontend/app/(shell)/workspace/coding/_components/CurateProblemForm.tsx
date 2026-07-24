"use client";

/**
 * Admin form for create/edit of curated coding problems.
 * Tests: first 2 = sample (shown), rest = hidden (grading).
 */

import { useEffect, useState } from "react";
import {
  Button,
  Group,
  Paper,
  Select,
  Stack,
  Switch,
  TagsInput,
  Text,
  TextInput,
  Textarea,
  Title,
} from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { useRouter } from "next/navigation";
import {
  useCodingCurateActions,
  type CodingEditorialProblem,
  type CodingTestCase,
} from "@/lib/api/queries";

type FormState = {
  title: string;
  statement: string;
  starter_code: string;
  difficulty: "easy" | "medium" | "hard";
  language_id: number;
  concept: string;
  tags: string[];
  editor_solution: string;
  published: boolean;
  tests: CodingTestCase[];
};

const EMPTY: FormState = {
  title: "",
  statement: "",
  starter_code: "print(0)\n",
  difficulty: "medium",
  language_id: 71,
  concept: "",
  tags: [],
  editor_solution: "",
  published: true,
  tests: [
    { stdin: "", expected_output: "" },
    { stdin: "", expected_output: "" },
    { stdin: "", expected_output: "" },
  ],
};

export function CurateProblemForm({
  mode,
  initial,
  assertionId,
}: {
  mode: "create" | "edit";
  initial?: CodingEditorialProblem | null;
  assertionId?: string;
}) {
  const router = useRouter();
  const actions = useCodingCurateActions();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!initial) return;
    const tests = [
      ...(initial.sample_tests ?? []),
      ...(initial.hidden_tests ?? []),
    ];
    while (tests.length < 3) tests.push({ stdin: "", expected_output: "" });
    // eslint-disable-next-line react-hooks/set-state-in-effect -- seed form from loaded editorial problem
    setForm({
      title: initial.title ?? "",
      statement: initial.statement ?? "",
      starter_code: initial.starter_code ?? "",
      difficulty: initial.difficulty ?? "medium",
      language_id: initial.language_id ?? 71,
      concept: initial.concept ?? "",
      tags: initial.tags ?? [],
      editor_solution: initial.editor_solution ?? "",
      published: initial.published !== false,
      tests,
    });
  }, [initial]);

  function updateTest(i: number, patch: Partial<CodingTestCase>) {
    setForm((f) => ({
      ...f,
      tests: f.tests.map((t, idx) => (idx === i ? { ...t, ...patch } : t)),
    }));
  }

  async function handleSave() {
    if (!form.title.trim() || !form.statement.trim()) {
      notifications.show({ title: "Missing fields", message: "Title and statement required.", color: "terracotta" });
      return;
    }
    if (form.tests.length < 3) {
      notifications.show({
        title: "Need more tests",
        message: "At least 3 tests (2 sample + 1 hidden).",
        color: "terracotta",
      });
      return;
    }
    setBusy(true);
    try {
      const body = {
        title: form.title.trim(),
        statement: form.statement.trim(),
        starter_code: form.starter_code,
        difficulty: form.difficulty,
        language_id: form.language_id,
        concept: form.concept.trim(),
        tags: form.tags,
        editor_solution: form.editor_solution,
        published: form.published,
        tests: form.tests.map((t) => ({
          stdin: t.stdin,
          expected_output: t.expected_output,
        })),
      };
      if (mode === "create") {
        const created = await actions.create(body);
        actions.invalidate();
        notifications.show({ title: "Published", message: created.title, color: "sage" });
        router.push(`/workspace/coding/${created.id}/edit`);
      } else if (assertionId) {
        await actions.update(assertionId, body);
        actions.invalidate();
        notifications.show({ title: "Saved", message: form.title, color: "sage" });
      }
    } catch (e) {
      notifications.show({
        title: "Save failed",
        message: e instanceof Error ? e.message : "Unknown error",
        color: "terracotta",
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!assertionId) return;
    if (!window.confirm("Unpublish and deactivate this problem?")) return;
    setBusy(true);
    try {
      await actions.remove(assertionId);
      actions.invalidate();
      notifications.show({ title: "Removed", message: "Problem unpublished.", color: "sage" });
      router.push("/workspace/coding");
    } catch (e) {
      notifications.show({
        title: "Delete failed",
        message: e instanceof Error ? e.message : "Unknown error",
        color: "terracotta",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap="md" pb="xl">
      <Group justify="space-between" wrap="wrap">
        <Title order={3} ff="var(--font-serif)" fw={500}>
          {mode === "create" ? "New coding problem" : "Edit problem"}
        </Title>
        <Group gap="xs">
          <Button variant="subtle" radius="xl" onClick={() => router.push("/workspace/coding")}>
            Back
          </Button>
          {mode === "edit" ? (
            <Button variant="light" color="terracotta" radius="xl" loading={busy} onClick={() => void handleDelete()}>
              Unpublish
            </Button>
          ) : null}
          <Button color="lavender" radius="xl" loading={busy} onClick={() => void handleSave()}>
            Save
          </Button>
        </Group>
      </Group>

      <Paper radius="lg" p="md" withBorder>
        <Stack gap="sm">
          <TextInput
            label="Title"
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.currentTarget.value }))}
            radius="md"
          />
          <Textarea
            label="Statement"
            description="Include exact stdin / stdout format and a worked example."
            value={form.statement}
            onChange={(e) => setForm((f) => ({ ...f, statement: e.currentTarget.value }))}
            autosize
            minRows={6}
            radius="md"
            styles={{ input: { fontFamily: "var(--font-serif)", lineHeight: 1.55 } }}
          />
          <Group grow align="flex-start">
            <Select
              label="Difficulty"
              value={form.difficulty}
              onChange={(v) =>
                setForm((f) => ({ ...f, difficulty: (v as FormState["difficulty"]) || "medium" }))
              }
              data={[
                { value: "easy", label: "Easy" },
                { value: "medium", label: "Medium" },
                { value: "hard", label: "Hard" },
              ]}
              allowDeselect={false}
              radius="md"
            />
            <TextInput
              label="Concept"
              value={form.concept}
              onChange={(e) => setForm((f) => ({ ...f, concept: e.currentTarget.value }))}
              radius="md"
            />
          </Group>
          <TagsInput
            label="Tags"
            description="e.g. arrays, stack, dp"
            value={form.tags}
            onChange={(tags) => setForm((f) => ({ ...f, tags }))}
            radius="md"
          />
          <Textarea
            label="Starter code"
            value={form.starter_code}
            onChange={(e) => setForm((f) => ({ ...f, starter_code: e.currentTarget.value }))}
            autosize
            minRows={4}
            radius="md"
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace, monospace)", fontSize: 13 } }}
          />
          <Textarea
            label="Reference solution (editorial only)"
            description="Never shown to learners. Optional but useful for verify."
            value={form.editor_solution}
            onChange={(e) => setForm((f) => ({ ...f, editor_solution: e.currentTarget.value }))}
            autosize
            minRows={4}
            radius="md"
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace, monospace)", fontSize: 13 } }}
          />
          <Switch
            label="Published"
            description="Unpublished drafts stay off the public bank."
            checked={form.published}
            onChange={(e) => setForm((f) => ({ ...f, published: e.currentTarget.checked }))}
          />
        </Stack>
      </Paper>

      <Paper radius="lg" p="md" withBorder>
        <Group justify="space-between" mb="sm">
          <BoxHeading />
          <Button
            size="xs"
            variant="light"
            radius="xl"
            leftSection={<IconPlus size={14} />}
            onClick={() =>
              setForm((f) => ({
                ...f,
                tests: [...f.tests, { stdin: "", expected_output: "" }],
              }))
            }
          >
            Add test
          </Button>
        </Group>
        <Stack gap="sm">
          {form.tests.map((t, i) => (
            <Paper key={i} radius="md" p="sm" withBorder bg="gray.0">
              <Group justify="space-between" mb={6}>
                <Text fz="xs" fw={600} c="dimmed">
                  Case {i + 1}
                  {i < 2 ? " · sample" : " · hidden"}
                </Text>
                {form.tests.length > 3 ? (
                  <Button
                    size="compact-xs"
                    variant="subtle"
                    color="terracotta"
                    leftSection={<IconTrash size={12} />}
                    onClick={() =>
                      setForm((f) => ({
                        ...f,
                        tests: f.tests.filter((_, idx) => idx !== i),
                      }))
                    }
                  >
                    Remove
                  </Button>
                ) : null}
              </Group>
              <Stack gap={6}>
                <Textarea
                  label="stdin"
                  value={t.stdin}
                  onChange={(e) => updateTest(i, { stdin: e.currentTarget.value })}
                  autosize
                  minRows={2}
                  radius="md"
                  styles={{ input: { fontFamily: "monospace", fontSize: 12 } }}
                />
                <Textarea
                  label="expected stdout"
                  value={t.expected_output}
                  onChange={(e) => updateTest(i, { expected_output: e.currentTarget.value })}
                  autosize
                  minRows={2}
                  radius="md"
                  styles={{ input: { fontFamily: "monospace", fontSize: 12 } }}
                />
              </Stack>
            </Paper>
          ))}
        </Stack>
      </Paper>
    </Stack>
  );
}

function BoxHeading() {
  return (
    <Stack gap={0}>
      <Text fw={600} fz="sm">
        Test cases
      </Text>
      <Text fz="xs" c="dimmed">
        First two shown as samples; remaining grade on Submit.
      </Text>
    </Stack>
  );
}
