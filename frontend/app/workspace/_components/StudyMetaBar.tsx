"use client";

import { Fragment } from "react";
import {
  Box,
  Group,
  Menu,
  Stack,
  Text,
  ThemeIcon,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import {
  IconAdjustmentsHorizontal,
  IconBook,
  IconBuildingMonument,
  IconBulb,
  IconCards,
  IconCheck,
  IconChevronDown,
  IconClipboardList,
  IconNotes,
  IconPencilQuestion,
  IconSparkles,
  type Icon,
} from "@tabler/icons-react";
import { type StudyMode } from "@/app/workspace/_components/studyNav";

/**
 * The study modes, grouped the same two ways as the desktop switcher: work
 * directly with the material vs. the AI-generated study aids. Drives the compact
 * (mobile) mode dropdown.
 */
const MODE_GROUPS: { label: string; modes: { value: StudyMode; label: string; icon: Icon }[] }[] = [
  {
    label: "Work with the material",
    modes: [
      { value: "read", label: "Read", icon: IconBook },
      { value: "learn", label: "Learn", icon: IconBulb },
      { value: "test", label: "Test", icon: IconClipboardList },
    ],
  },
  {
    label: "AI study aids",
    modes: [
      { value: "explain", label: "Explain", icon: IconSparkles },
      { value: "notes", label: "Notes", icon: IconNotes },
      { value: "cards", label: "Cards", icon: IconCards },
      { value: "palace", label: "Memory Palace", icon: IconBuildingMonument },
      { value: "quiz", label: "Quiz", icon: IconPencilQuestion },
    ],
  },
];
const ALL_MODES = MODE_GROUPS.flatMap((g) => g.modes);

/**
 * Study meta bar (extracted from the workspace page monolith): the top strip with
 * the mode badge + progress, the compact (mobile) mode dropdown
 * (CompactModeSelect), and the Adaptive/Classic chooser menu.
 */
export function StudyMetaBar({
  questionIndex,
  questionTotal,
  page,
  mode,
  onModeChange,
  showProgress = true,
  compact = false,
  studyMode,
  onStudyModeChange,
}: {
  questionIndex: number;
  questionTotal: number;
  /** The page the learner is currently on (shown next to question progress). */
  page?: number;
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
      {page ? (
        <Text
          size="xs"
          c="dimmed"
          fw={600}
          style={{ flexShrink: 0, letterSpacing: "0.01em" }}
        >
          Page {page}
        </Text>
      ) : null}
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

  if (compact) {
    // Phones can't fit a left mode rail (or 8 segments in a row), so the switch
    // becomes a single full-width dropdown above the progress.
    return (
      <Stack px="sm" py={6} gap={6} style={{ flexShrink: 0 }}>
        <CompactModeSelect mode={mode} onChange={onModeChange} />
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

/**
 * Compact (mobile) mode switcher: a single full-width dropdown showing the
 * current mode, opening the full grouped list. Replaces the two segmented
 * controls, which overflowed into a cramped horizontal-scroll strip on phones.
 */
function CompactModeSelect({
  mode,
  onChange,
}: {
  mode: StudyMode;
  onChange: (mode: StudyMode) => void;
}) {
  const current = ALL_MODES.find((m) => m.value === mode) ?? ALL_MODES[0];
  const CurrentIcon = current.icon;
  return (
    <Menu shadow="md" width="target" position="bottom-start" radius="md" withinPortal>
      <Menu.Target>
        <UnstyledButton
          aria-label={`Study mode: ${current.label}. Tap to switch.`}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            width: "100%",
            padding: "9px 12px",
            borderRadius: 12,
            border: "1px solid var(--mantine-color-default-border)",
            background: "var(--mantine-color-body)",
          }}
        >
          <ThemeIcon
            size={24}
            radius="md"
            variant="light"
            color="lavender"
            style={{ color: "light-dark(var(--mantine-color-lavender-7), var(--mantine-color-lavender-3))" }}
          >
            <CurrentIcon size={15} stroke={2} />
          </ThemeIcon>
          <Text fz="sm" fw={600} c="var(--mantine-color-text)" style={{ flex: 1, textAlign: "left" }}>
            {current.label}
          </Text>
          <IconChevronDown size={16} stroke={2} style={{ color: "var(--mantine-color-dimmed)" }} />
        </UnstyledButton>
      </Menu.Target>
      <Menu.Dropdown>
        {MODE_GROUPS.map((group, gi) => (
          <Fragment key={group.label}>
            {gi > 0 ? <Menu.Divider /> : null}
            <Menu.Label>{group.label}</Menu.Label>
            {group.modes.map((m) => {
              const Icon = m.icon;
              const active = m.value === mode;
              return (
                <Menu.Item
                  key={m.value}
                  onClick={() => onChange(m.value)}
                  leftSection={
                    <Icon
                      size={17}
                      stroke={1.9}
                      color={active ? "var(--mantine-color-lavender-6)" : "var(--mantine-color-dimmed)"}
                    />
                  }
                  rightSection={
                    active ? <IconCheck size={15} stroke={2.4} color="var(--mantine-color-lavender-6)" /> : null
                  }
                >
                  <Text fz="sm" fw={active ? 600 : 500}>
                    {m.label}
                  </Text>
                </Menu.Item>
              );
            })}
          </Fragment>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}
