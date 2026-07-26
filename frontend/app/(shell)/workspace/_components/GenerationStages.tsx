"use client";

import { Box, Stack, Text } from "@mantine/core";
import { IconCheck } from "@tabler/icons-react";

/**
 * Plain-language progress for the question-generation wait. Instead of a vague
 * spinner, it shows the three real steps - read the pages, decide how many
 * questions (the "budget"), write them - and which one is happening now, so a
 * first-time user (or a 10-year-old) understands what's taking the few seconds.
 */
export function GenerationStages({
  artifactStatus,
  indexProgress,
  ragWindowReady,
  pageTriageComplete,
  generationPending,
  questionsGenerated,
  questionBudget,
  compact = false,
  isDark = false,
}: {
  artifactStatus?: string;
  indexProgress?: number;
  ragWindowReady?: boolean;
  pageTriageComplete?: boolean;
  generationPending?: boolean;
  questionsGenerated?: number;
  questionBudget?: number;
  compact?: boolean;
  isDark?: boolean;
}) {
  const budget = questionBudget ?? 0;
  const generated = questionsGenerated ?? 0;

  const reading = artifactStatus === "indexing" || ragWindowReady === false;
  const readDone = !reading;
  const planning = readDone && !pageTriageComplete;
  const planDone = readDone && Boolean(pageTriageComplete);
  // Writing stage is "get the first card ready" — more questions refill in the
  // background while the learner studies. Do not keep this stage active until
  // the full page budget is filled (that can be dozens/hundreds of MCQs).
  const writing =
    planDone && generated < 1 && (Boolean(generationPending) || budget > 0);

  type State = "done" | "active" | "todo";
  const s1: State = reading ? "active" : "done";
  const s2: State = !readDone ? "todo" : planning ? "active" : "done";
  const s3: State = !planDone ? "todo" : writing ? "active" : "done";

  const stages: { state: State; title: string; sub: string }[] = [
    {
      state: s1,
      title: "Reading your pages",
      sub:
        artifactStatus === "indexing"
          ? `Getting your document ready${indexProgress ? ` · ${indexProgress}%` : ""}`
          : s1 === "active"
            ? "Looking at what you chose to study"
            : "Done",
    },
    {
      state: s2,
      title: "Planning the quiz",
      sub:
        s2 === "todo"
          ? "Up next"
          : s2 === "active"
            ? "Counting the distinct ideas worth testing here"
            : budget > 0
              ? `About ${budget} ideas to cover - questions arrive a few at a time`
              : "Done",
    },
    {
      state: s3,
      title: "Writing your questions",
      sub:
        s3 === "todo"
          ? "Up next"
          : s3 === "done"
            ? generated > 0
              ? "First ones ready - more while you study"
              : "Done"
            : generated > 0
              ? `${generated} ready - more on the way`
              : "Writing your first question so you can start",
    },
  ];

  return (
    <Stack gap="xs" w="100%">
      <style>{`
        @keyframes zv-stage-pulse { 0%,100% { transform: scale(1); opacity: 1; } 50% { transform: scale(0.7); opacity: 0.5; } }
        .zv-stage-dot { animation: zv-stage-pulse 1.2s ease-in-out infinite; }
        @media (prefers-reduced-motion: reduce) { .zv-stage-dot { animation: none !important; } }
      `}</style>
      {stages.map((st, i) => {
        const last = i === stages.length - 1;
        const dotColor =
          st.state === "done"
            ? "var(--mantine-color-sage-6)"
            : st.state === "active"
              ? "var(--mantine-color-lavender-6)"
              : isDark
                ? "var(--mantine-color-dark-2)"
                : "var(--mantine-color-gray-4)";
        return (
          <Box
            key={i}
            p={st.state === "active" ? (compact ? "xs" : "sm") : compact ? "2px 0" : "4px 0"}
            style={{
              borderRadius: "var(--mantine-radius-lg)",
              background:
                st.state === "active"
                  ? isDark
                    ? "var(--mantine-color-lavender-1)"
                    : "var(--mantine-color-lavender-0)"
                  : undefined,
              border:
                st.state === "active"
                  ? `1px solid var(--mantine-color-lavender-${isDark ? 3 : 2})`
                  : undefined,
            }}
          >
            <Box style={{ display: "flex", gap: 12, alignItems: "stretch" }}>
              <Box style={{ display: "flex", flexDirection: "column", alignItems: "center", flexShrink: 0 }}>
                <Box
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: "50%",
                    display: "grid",
                    placeItems: "center",
                    flexShrink: 0,
                    background: st.state === "done" ? "var(--mantine-color-sage-6)" : "transparent",
                    border: st.state === "done" ? "none" : `2px solid ${dotColor}`,
                  }}
                >
                  {st.state === "done" ? (
                    <IconCheck size={12} stroke={3} color={isDark ? "#1A1917" : "#FFFFFF"} />
                  ) : st.state === "active" ? (
                    <Box
                      className="zv-stage-dot"
                      style={{ width: 8, height: 8, borderRadius: "50%", background: dotColor }}
                    />
                  ) : null}
                </Box>
                {!last ? (
                  <Box
                    style={{
                      width: 2,
                      flex: 1,
                      minHeight: 18,
                      marginTop: 2,
                      marginBottom: 2,
                      background:
                        st.state === "done"
                          ? "var(--mantine-color-sage-4)"
                          : isDark
                            ? "var(--mantine-color-dark-3)"
                            : "var(--mantine-color-gray-4)",
                    }}
                  />
                ) : null}
              </Box>
              <Box style={{ paddingBottom: last ? 0 : compact ? 6 : 10, textAlign: "left", minWidth: 0 }}>
                <Text
                  fz={compact ? "sm" : "md"}
                  fw={st.state === "active" ? 600 : 500}
                  c={st.state === "todo" ? "dimmed" : "var(--mantine-color-text)"}
                  style={{ lineHeight: 1.25, letterSpacing: "-0.01em" }}
                >
                  {st.title}
                </Text>
                <Text fz="xs" c="dimmed" style={{ lineHeight: 1.45 }}>
                  {st.sub}
                </Text>
              </Box>
            </Box>
          </Box>
        );
      })}
      <Text fz="xs" c="dimmed" mt={compact ? 4 : "xs"} lh={1.5}>
        Zivo reads your pages and writes fresh questions. This short wait is normal.
      </Text>
    </Stack>
  );
}
