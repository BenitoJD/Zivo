"use client";

import {
  Box,
  Group,
  Menu,
  SegmentedControl,
  Stack,
  Text,
  ThemeIcon,
  Tooltip,
  UnstyledButton,
  useMantineColorScheme,
} from "@mantine/core";
import {
  IconAdjustmentsHorizontal,
  IconBulb,
  IconCheck,
  IconChevronDown,
  IconClipboardList,
} from "@tabler/icons-react";
import { type StudyMode } from "@/app/workspace/_components/studyNav";

/**
 * Study meta bar (extracted from the workspace page monolith): the top strip with
 * the mode badge + progress, the Learn/Test/aids mode switcher (StudyModeSwitch),
 * and the Adaptive/Classic chooser menu.
 */
export function StudyMetaBar({
  questionIndex,
  questionTotal,
  mode,
  onModeChange,
  showProgress = true,
  compact = false,
  studyMode,
  onStudyModeChange,
}: {
  questionIndex: number;
  questionTotal: number;
  mode: StudyMode;
  onModeChange: (mode: StudyMode) => void;
  showProgress?: boolean;
  compact?: boolean;
  studyMode?: "adaptive" | "classic";
  onStudyModeChange?: (mode: "adaptive" | "classic") => void;
}) {
  const showBar = showProgress && questionTotal > 0;
  const pct = showBar ? Math.min(100, Math.round((questionIndex / questionTotal) * 100)) : 0;
  const segmented = showBar && questionTotal <= 16;
  const isTestMode = mode === "test";
  // Test wears the brand's deep green; Learn keeps lavender — a constant, glanceable
  // signal that the two are different study contexts.
  const barAccent = isTestMode ? "forest" : "lavender";
  const modeBadge =
    mode === "learn" || mode === "test" ? (
      <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
        <ThemeIcon
          size={20}
          radius="xl"
          variant="light"
          color={barAccent}
          // Brighten the glyph in dark mode so the badge icon stays legible.
          style={{ color: `light-dark(var(--mantine-color-${barAccent}-7), var(--mantine-color-${barAccent}-3))` }}
        >
          {isTestMode ? <IconClipboardList size={12} stroke={2} /> : <IconBulb size={12} stroke={2} />}
        </ThemeIcon>
        <Text
          fz="xs"
          fw={700}
          tt="uppercase"
          style={{
            letterSpacing: "0.04em",
            color: `light-dark(var(--mantine-color-${barAccent}-${isTestMode ? 8 : 7}), var(--mantine-color-${barAccent}-3))`,
          }}
        >
          {isTestMode ? "Test" : "Learn"}
        </Text>
      </Group>
    ) : null;

  // Adaptive vs Classic, switchable per document. Adaptive picks each next
  // question at the learner's edge; Classic walks a fixed set in order.
  const currentStudyMode = studyMode ?? "adaptive";
  const studyModeControl =
    (mode === "learn" || mode === "test") && onStudyModeChange ? (
      <Menu shadow="md" width={244} position="bottom-end" radius="md" withinPortal>
        <Menu.Target>
          <UnstyledButton
            aria-label="Change how questions are chosen"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              flexShrink: 0,
              padding: "5px 10px",
              borderRadius: 999,
              border: "1px solid var(--mantine-color-default-border)",
              background: "var(--mantine-color-body)",
            }}
          >
            <IconAdjustmentsHorizontal size={14} stroke={1.8} style={{ color: "var(--mantine-color-dimmed)" }} />
            <Text fz="xs" fw={600} c="var(--mantine-color-text)">
              {currentStudyMode === "classic" ? "Classic" : "Adaptive"}
            </Text>
            <IconChevronDown size={12} stroke={2} style={{ color: "var(--mantine-color-dimmed)" }} />
          </UnstyledButton>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Label>How questions are chosen</Menu.Label>
          <Menu.Item
            onClick={() => onStudyModeChange("adaptive")}
            rightSection={currentStudyMode !== "classic" ? <IconCheck size={15} stroke={2.4} color="var(--mantine-color-lavender-6)" /> : null}
          >
            <Text fz="sm" fw={500}>Adaptive tutor</Text>
            <Text fz="xs" c="dimmed">Questions adjust to your answers</Text>
          </Menu.Item>
          <Menu.Item
            onClick={() => onStudyModeChange("classic")}
            rightSection={currentStudyMode === "classic" ? <IconCheck size={15} stroke={2.4} color="var(--mantine-color-lavender-6)" /> : null}
          >
            <Text fz="sm" fw={500}>Classic</Text>
            <Text fz="xs" c="dimmed">A fixed set, in order</Text>
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    ) : null;

  const progress = !showBar ? null : (
    <Group gap={compact ? 8 : 12} wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
      <Tooltip
        label={`Question ${questionIndex} of ${questionTotal}. The total is sized to this page — roughly one question per distinct idea worth testing.`}
        position="bottom"
        withArrow
        multiline
        w={250}
        openDelay={250}
      >
        <Text size="xs" c="dimmed" fw={600} ff="monospace" style={{ flexShrink: 0, letterSpacing: "0.02em", cursor: "help" }}>
          {String(questionIndex).padStart(2, "0")}
          <Text component="span" inherit style={{ opacity: 0.45 }}>
            {" / "}
            {String(questionTotal).padStart(2, "0")}
          </Text>
        </Text>
      </Tooltip>
      {segmented ? (
        <Group gap={4} wrap="nowrap" style={{ flex: 1, minWidth: 0, maxWidth: 380 }}>
          {Array.from({ length: questionTotal }).map((_, i) => (
            <Box
              key={i}
              style={{
                flex: 1,
                height: 5,
                borderRadius: 99,
                background: i < questionIndex ? `var(--mantine-color-${barAccent}-6)` : "var(--mantine-color-gray-3)",
                transition: "background 260ms ease",
              }}
            />
          ))}
        </Group>
      ) : (
        <Box style={{ flex: 1, maxWidth: 380, height: 5, borderRadius: 99, background: "var(--mantine-color-gray-3)", overflow: "hidden" }}>
          <Box style={{ width: `${pct}%`, height: "100%", borderRadius: 99, background: `var(--mantine-color-${barAccent}-6)`, transition: "width 320ms cubic-bezier(0.32,0.72,0,1)" }} />
        </Box>
      )}
    </Group>
  );

  // The 7-mode switch can't fit a phone row, so on compact it gets its own
  // full-width, horizontally-scrollable row beneath the progress.
  const modeSwitch = (
    <Box
      className="zv-modeswitch-scroll"
      style={{ minWidth: 0, maxWidth: "100%", overflowX: "auto", overflowY: "hidden", scrollbarWidth: "none" }}
    >
      <StudyModeSwitch mode={mode} onChange={onModeChange} compact={compact} />
    </Box>
  );

  if (compact) {
    // Phones can't fit a left mode rail, so the switch lives here above the progress.
    return (
      <Stack px="sm" py={6} gap={6} style={{ flexShrink: 0 }}>
        <style>{`.zv-modeswitch-scroll::-webkit-scrollbar { display: none; }`}</style>
        {modeSwitch}
        {progress || studyModeControl ? (
          <Group justify="space-between" wrap="nowrap" align="center" gap="sm">
            <Box style={{ flex: 1, minWidth: 0 }}>{progress}</Box>
            {studyModeControl}
          </Group>
        ) : null}
      </Stack>
    );
  }

  // Desktop/tablet: the sidebar owns mode switching, so the top bar carries the
  // mode identity + question progress + the Adaptive/Classic chooser — and nothing
  // at all in modes that have none (e.g. Read), so the content starts cleanly.
  if (!progress && !modeBadge && !studyModeControl) return null;
  return (
    <Group
      px={{ base: "sm", sm: "md", lg: "lg" }}
      py={8}
      justify="space-between"
      align="center"
      wrap="nowrap"
      gap="md"
      style={{ flexShrink: 0 }}
    >
      <Group gap="md" wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
        {modeBadge}
        {progress}
      </Group>
      {studyModeControl}
    </Group>
  );
}

function StudyModeSwitch({
  mode,
  onChange,
  compact = false,
}: {
  mode: StudyMode;
  onChange: (mode: StudyMode) => void;
  compact?: boolean;
}) {
  const { colorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  type Mode = StudyMode;

  // Two intuitive groups instead of one crowded row: work directly with the material
  // (read / learn / test) vs. the AI study aids it generates.
  const core = ["read", "learn", "test"];
  const tools = ["explain", "notes", "cards", "palace", "quiz"];
  const styles = {
    root: {
      background: isDark ? "var(--mantine-color-dark-6)" : "var(--mantine-color-gray-1)",
      border: `1px solid ${isDark ? "var(--mantine-color-dark-4)" : "var(--mantine-color-gray-3)"}`,
    },
    label: {
      fontWeight: 600,
      paddingInline: compact ? 10 : 13,
      fontSize: compact ? 11 : 12,
      letterSpacing: "-0.01em",
    },
    indicator: { boxShadow: "none" },
  };

  return (
    <Group gap={compact ? 6 : 8} wrap="nowrap">
      <SegmentedControl
        size="xs"
        radius="xl"
        value={core.includes(mode) ? mode : ""}
        onChange={(v) => v && onChange(v as Mode)}
        data={[
          { label: "Read", value: "read" },
          { label: "Learn", value: "learn" },
          { label: "Test", value: "test" },
        ]}
        styles={styles}
      />
      <SegmentedControl
        size="xs"
        radius="xl"
        value={tools.includes(mode) ? mode : ""}
        onChange={(v) => v && onChange(v as Mode)}
        data={[
          { label: "Explain", value: "explain" },
          { label: "Notes", value: "notes" },
          { label: "Cards", value: "cards" },
          { label: "Palace", value: "palace" },
          { label: "Quiz", value: "quiz" },
        ]}
        styles={styles}
      />
    </Group>
  );
}
