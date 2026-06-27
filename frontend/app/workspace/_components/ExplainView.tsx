"use client";

import { useEffect, useState } from "react";
import {
  Accordion,
  Box,
  Button,
  Center,
  Group,
  List,
  Loader,
  Stack,
  Text,
  ThemeIcon,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconBulb,
  IconListSearch,
} from "@tabler/icons-react";
import {
  useTopicExplanationQuery,
  useTopicsQuery,
  type Topic,
} from "@/lib/api/queries";
import { GenerateGate, markGenStarted, readGenStarted } from "./GenerateGate";
import { WaitState } from "./WaitState";

/**
 * Explain mode — a plain-language tour of a document's topics. Additive to MCQ:
 * the LLM maps the whole source into topics; opening one shows a high-level,
 * step-by-step explanation grounded in the source. Calm Paper styling throughout.
 */
export function ExplainView({
  artifactId,
  compact = false,
}: {
  artifactId: string;
  compact?: boolean;
}) {
  const [started, setStarted] = useState(false);
  useEffect(() => {
    if (readGenStarted(artifactId, "explain")) setStarted(true);
  }, [artifactId]);

  const { data, isError, refetch } = useTopicsQuery(artifactId, started);
  const [open, setOpen] = useState<string | null>(null);

  const status = data?.status;
  const topics = data?.topics ?? [];

  function start() {
    markGenStarted(artifactId, "explain");
    setStarted(true);
  }

  if (!started && topics.length === 0) {
    return (
      <GenerateGate
        icon={<IconBulb size={28} />}
        title="Explain this source"
        description="Turn this source into a clear topic-by-topic explanation in plain language."
        onStart={start}
        compact={compact}
      />
    );
  }

  if (isError || status === "failed") {
    return (
      <WaitState
        icon={<IconAlertTriangle size={26} />}
        title="Couldn’t map the topics"
        body="Something went wrong reading this material. Try again in a moment."
        action={
          <Button variant="light" color="lavender" radius="xl" onClick={() => void refetch()}>
            Try again
          </Button>
        }
      />
    );
  }

  if (!status || status === "indexing" || status === "generating" || topics.length === 0) {
    return (
      <WaitState
        pet
        title="Mapping the topics"
        body="Reading the whole document and laying out what’s worth understanding…"
      />
    );
  }

  return (
    <Stack gap="lg" pb="xl">
      <Stack gap={4}>
        <Group gap={8}>
          <ThemeIcon variant="light" color="lavender" radius="xl" size="md">
            <IconListSearch size={16} />
          </ThemeIcon>
          <Text ff="var(--font-serif)" fz={compact ? 22 : 26} fw={500} c="var(--mantine-color-text)">
            What this covers
          </Text>
        </Group>
        <Text c="dimmed" fz="sm">
          {topics.length} topics, in reading order. Open any one for a plain-language explanation.
        </Text>
      </Stack>

      <Accordion
        value={open}
        onChange={setOpen}
        radius="xl"
        variant="separated"
        chevronPosition="right"
        styles={{
          item: {
            backgroundColor: "var(--mantine-color-gray-0)",
            border: "1px solid var(--app-border, var(--mantine-color-gray-2))",
          },
          content: { paddingTop: 0 },
        }}
      >
        {topics.map((topic, i) => (
          <Accordion.Item key={topic.key} value={topic.key}>
            <Accordion.Control>
              <Group gap="sm" wrap="nowrap" align="flex-start">
                <Text c="lavender.6" fw={600} fz="sm" w={22} ta="right" style={{ flexShrink: 0 }}>
                  {i + 1}
                </Text>
                <Box>
                  <Text ff="var(--font-serif)" fw={500} fz={compact ? 16 : 17} lh={1.3}>
                    {topic.title}
                  </Text>
                  {topic.summary ? (
                    <Text c="dimmed" fz="sm" mt={2} lineClamp={2}>
                      {topic.summary}
                    </Text>
                  ) : null}
                </Box>
              </Group>
            </Accordion.Control>
            <Accordion.Panel>
              {open === topic.key ? (
                <TopicExplanation artifactId={artifactId} topic={topic} />
              ) : null}
            </Accordion.Panel>
          </Accordion.Item>
        ))}
      </Accordion>
    </Stack>
  );
}

function TopicExplanation({ artifactId, topic }: { artifactId: string; topic: Topic }) {
  const { data, isError, refetch } = useTopicExplanationQuery(artifactId, topic.key);

  if (isError || data?.status === "failed") {
    return (
      <Group gap="sm" py="sm">
        <Text c="dimmed" fz="sm">
          Couldn’t generate this explanation just now.
        </Text>
        <Button size="xs" variant="subtle" color="lavender" onClick={() => void refetch()}>
          Retry
        </Button>
      </Group>
    );
  }

  if (!data || data.status !== "ready" || !data.explanation) {
    return (
      <Group gap="xs" py="md" c="dimmed">
        <Loader color="lavender" size="xs" />
        <Text fz="sm">Explaining it simply…</Text>
      </Group>
    );
  }

  return (
    <Box pl={{ base: 0, sm: 34 }} pt={4}>
      <Group gap={6} mb="xs" c="lavender.6">
        <IconBulb size={15} />
        <Text fz="xs" fw={600} tt="uppercase" lts={0.4}>
          In plain words
        </Text>
      </Group>
      <ExplanationBody text={data.explanation} />
    </Box>
  );
}

/**
 * Lightweight renderer: paragraphs split on blank lines, with simple bullet lists.
 * A block may mix a lead-in line with bullet lines ("Here are the types:\n- A\n- B");
 * we render the lead-in as text and the trailing bullets as a list so newlines don't
 * collapse into one run-on line.
 */
const BULLET = /^[-*•]\s+/;

function ExplanationBody({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  return (
    <Stack gap="sm">
      {blocks.map((block, i) => {
        const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);
        const lead = lines.filter((l) => !BULLET.test(l));
        const bullets = lines.filter((l) => BULLET.test(l));
        const leadText = lead.join(" ");
        return (
          <Stack key={i} gap={6}>
            {leadText ? (
              <Text fz="sm" lh={1.65} c="var(--mantine-color-text)">
                {leadText}
              </Text>
            ) : null}
            {bullets.length ? (
              <List spacing={4} size="sm" c="var(--mantine-color-text)">
                {bullets.map((l, j) => (
                  <List.Item key={j}>{l.replace(BULLET, "")}</List.Item>
                ))}
              </List>
            ) : null}
          </Stack>
        );
      })}
    </Stack>
  );
}