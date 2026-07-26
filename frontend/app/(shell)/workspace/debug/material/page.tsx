"use client";

import { useState } from "react";
import {
  Box,
  Button,
  NumberInput,
  Paper,
  ScrollArea,
  Stack,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useRouter } from "next/navigation";
import { useDebugActions, useDebugCookJobQuery } from "@/lib/api/queries";

export default function DebugCookMaterialPage() {
  const router = useRouter();
  const actions = useDebugActions();
  const [material, setMaterial] = useState("");
  const [brief, setBrief] = useState("");
  const [count, setCount] = useState(3);
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const jobQuery = useDebugCookJobQuery(jobId, Boolean(jobId));

  async function handleCook() {
    setSubmitting(true);
    try {
      const job = await actions.startCook({
        material,
        brief,
        scenario_count: count,
      });
      setJobId(job.id);
      notifications.show({ title: "Cooking started", message: "Scenarios are being generated.", color: "lavender" });
    } catch (e) {
      notifications.show({
        title: "Cook failed",
        message: e instanceof Error ? e.message : "Unknown error",
        color: "terracotta",
      });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmitLibrary() {
    if (!jobId) return;
    try {
      await actions.submitToLibrary(jobId);
      actions.invalidateCook(jobId);
      notifications.show({ title: "Submitted", message: "Sent to admin review queue.", color: "sage" });
    } catch (e) {
      notifications.show({
        title: "Submit failed",
        message: e instanceof Error ? e.message : "Unknown error",
        color: "terracotta",
      });
    }
  }

  const job = jobQuery.data;
  const done = job?.status === "done";
  const failed = job?.status === "failed";

  return (
    <ScrollArea h="100%" type="auto" offsetScrollbars>
      <Box maw={720} mx="auto" py="md" px={{ base: "xs", sm: "md" }}>
        <Stack gap="md">
          <Title order={3} ff="var(--font-serif)" fw={500}>
            Cook debug material
          </Title>
          <Text c="dimmed" size="sm">
            Paste code, logs, or notes. We generate diagnostic scenarios you can practice privately, then optionally submit to the public library.
          </Text>

          <Paper p="lg" radius="xl" withBorder>
            <Stack gap="md">
              <Textarea
                label="Material"
                description="Code snippet, stack trace, log excerpt, or study notes"
                minRows={8}
                value={material}
                onChange={(e) => setMaterial(e.currentTarget.value)}
              />
              <Textarea
                label="Cook brief (optional)"
                description='e.g. "3 medium scenarios about race conditions"'
                minRows={2}
                value={brief}
                onChange={(e) => setBrief(e.currentTarget.value)}
              />
              <NumberInput
                label="Scenario count"
                min={1}
                max={10}
                value={count}
                onChange={(v) => setCount(Number(v) || 3)}
              />
              <Button color="lavender" radius="xl" loading={submitting} onClick={() => void handleCook()}>
                Start cooking
              </Button>
            </Stack>
          </Paper>

          {job ? (
            <Paper p="lg" radius="xl" withBorder bg="gray.0">
              <Stack gap="sm">
                <Text fw={600}>Cook job: {job.status}</Text>
                {failed && job.error ? <Text c="terracotta" size="sm">{job.error}</Text> : null}
                {done && job.scenario_ids.length > 0 ? (
                  <>
                    <Text size="sm">Generated {job.scenario_ids.length} scenario(s).</Text>
                    <Button variant="light" radius="xl" onClick={() => router.push(`/practice/debug/${job.scenario_ids[0]}`)}>
                      Practice first scenario
                    </Button>
                    <Button variant="outline" radius="xl" onClick={() => void handleSubmitLibrary()}>
                      Submit to library
                    </Button>
                  </>
                ) : null}
              </Stack>
            </Paper>
          ) : null}
        </Stack>
      </Box>
    </ScrollArea>
  );
}
