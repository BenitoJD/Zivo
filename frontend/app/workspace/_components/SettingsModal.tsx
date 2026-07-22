"use client";

import { useState, type ReactNode } from "react";
import {
  Box,
  Button,
  Divider,
  Group,
  Modal,
  NumberInput,
  Paper,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  ThemeIcon,
  useMantineColorScheme,
} from "@mantine/core";
import { useLocalStorage } from "@mantine/hooks";
import {
  IconBook,
  IconCat,
  IconFlame,
  IconListNumbers,
  IconMoon,
  IconSparkles,
  IconSun,
} from "@tabler/icons-react";
import { CAT_ENABLED_KEY } from "@/app/_components/pets/PetPlayground";

type SettingsModalProps = {
  opened: boolean;
  onClose: () => void;
  username: string | null;
  onReplayOnboarding: () => void;
};

/** One setting: a tinted icon chip + title/description on the left, its control
 *  on the right. The uniform shape is what makes the panel read as one calm set. */
function SettingRow({
  icon,
  color,
  title,
  desc,
  control,
}: {
  icon: ReactNode;
  color: string;
  title: string;
  desc: string;
  control: ReactNode;
}) {
  return (
    <Group justify="space-between" align="center" wrap="nowrap" gap="md" px="md" py="sm">
      <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
        <ThemeIcon
          radius="md"
          size={34}
          variant="light"
          color={color}
          style={{
            flexShrink: 0,
            background: `var(--mantine-color-${color}-1)`,
            color: `var(--mantine-color-${color}-7)`,
            border: `1px solid var(--mantine-color-${color}-2)`,
          }}
        >
          {icon}
        </ThemeIcon>
        <Stack gap={0} style={{ minWidth: 0 }}>
          <Text fw={600} size="sm" lh={1.3}>
            {title}
          </Text>
          <Text size="xs" c="dimmed" lh={1.35} maw={230}>
            {desc}
          </Text>
        </Stack>
      </Group>
      <Box style={{ flexShrink: 0 }}>{control}</Box>
    </Group>
  );
}

const segStyles = { root: { background: "var(--mantine-color-default-hover)" } };

export function SettingsModal({ opened, onClose, username, onReplayOnboarding }: SettingsModalProps) {
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";

  const [studyMode, setStudyMode] = useState<"relaxed" | "exam">(() => {
    if (typeof window === "undefined") return "relaxed";
    return (localStorage.getItem("zivo-study-mode") as "relaxed" | "exam") || "relaxed";
  });
  const [mcqCount, setMcqCount] = useState<number>(() => {
    if (typeof window === "undefined") return 5;
    const stored = localStorage.getItem("zivo-mcq-count");
    return stored ? parseInt(stored, 10) || 5 : 5;
  });
  // Live (Mantine broadcasts to every PetPlayground) — flip and the cats appear
  // or vanish app-wide at once, no Save needed.
  const [catEnabled, setCatEnabled] = useLocalStorage({ key: CAT_ENABLED_KEY, defaultValue: false });

  const initial = (username ? username[0] : "g").toUpperCase();
  const displayName = username ? `@${username}` : "Guest";

  const saveSettings = () => {
    localStorage.setItem("zivo-study-mode", studyMode);
    localStorage.setItem("zivo-mcq-count", mcqCount.toString());
    onClose();
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={
        <Text fw={600} style={{ fontFamily: "var(--font-serif)", fontSize: "1.2rem", letterSpacing: "-0.01em" }}>
          Settings
        </Text>
      }
      size={468}
      radius="xl"
      overlayProps={{ backgroundOpacity: 0.5, blur: 10 }}
      styles={{
        header: { paddingBottom: 6, background: "var(--mantine-color-body)" },
        body: { paddingTop: 4 },
        content: { background: "var(--mantine-color-body)", border: "1px solid var(--mantine-color-default-border)" },
      }}
    >
      <Stack gap="md" pb={4}>
        {/* Profile — a soft gradient banner with a monogram avatar. */}
        <Paper
          radius="lg"
          p="md"
          style={{
            border: "1px solid var(--mantine-color-lavender-2)",
            background: isDark
              ? "linear-gradient(135deg, var(--mantine-color-lavender-1), var(--mantine-color-body))"
              : "linear-gradient(135deg, var(--mantine-color-lavender-0), #FFFFFF)",
          }}
        >
          <Group gap="md" wrap="nowrap">
            <Box
              style={{
                width: 46,
                height: 46,
                flexShrink: 0,
                borderRadius: 999,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "var(--mantine-color-lavender-2)",
                color: "var(--mantine-color-lavender-8)",
                fontWeight: 700,
                fontSize: 19,
              }}
            >
              {initial}
            </Box>
            <Stack gap={1} style={{ minWidth: 0 }}>
              <Text fw={600} size="sm">
                {displayName}
              </Text>
              <Text size="xs" c="dimmed">
                {username ? "Signed in" : "Studying as a guest"}
              </Text>
            </Stack>
          </Group>
        </Paper>

        {/* Grouped settings — one calm card, hairline-separated rows. */}
        <Paper radius="lg" p={0} withBorder style={{ overflow: "hidden", background: "var(--mantine-color-body)" }}>
          <SettingRow
            icon={<IconBook size={18} stroke={1.7} />}
            color="lavender"
            title="Study mode"
            desc="Relaxed gives unlimited hints; Exam is timed, no tutor."
            control={
              <SegmentedControl
                size="xs"
                value={studyMode}
                onChange={(v) => setStudyMode(v as "relaxed" | "exam")}
                data={[
                  { value: "relaxed", label: <Group gap={4} wrap="nowrap"><IconBook size={13} /><Text size="xs" fw={500}>Relaxed</Text></Group> },
                  { value: "exam", label: <Group gap={4} wrap="nowrap"><IconFlame size={13} /><Text size="xs" fw={500}>Exam</Text></Group> },
                ]}
                styles={segStyles}
              />
            }
          />
          <Divider color="var(--mantine-color-default-border)" />
          <SettingRow
            icon={<IconCat size={18} stroke={1.7} />}
            color="orange"
            title="Study cat"
            desc="A pixel cat roams the free space while you study. Applies everywhere. Off by default."
            control={
              <Switch
                checked={catEnabled}
                onChange={(e) => setCatEnabled(e.currentTarget.checked)}
                color="orange"
                size="lg"
                onLabel="ON"
                offLabel="OFF"
                aria-label={catEnabled ? "Turn study cat off" : "Turn study cat on"}
              />
            }
          />
          <Divider color="var(--mantine-color-default-border)" />
          <SettingRow
            icon={<IconListNumbers size={18} stroke={1.7} />}
            color="teal"
            title="Questions per source"
            desc="How many questions to generate for a new upload."
            control={
              <NumberInput
                value={mcqCount}
                onChange={(v) => setMcqCount(typeof v === "number" ? v : 5)}
                min={3}
                max={30}
                step={1}
                w={72}
                size="xs"
                styles={{ input: { textAlign: "center", fontWeight: 600 } }}
              />
            }
          />
          <Divider color="var(--mantine-color-default-border)" />
          <SettingRow
            icon={isDark ? <IconMoon size={18} stroke={1.7} /> : <IconSun size={18} stroke={1.7} />}
            color="indigo"
            title="Appearance"
            desc="Warm oat light, or deep ink dark."
            control={
              <SegmentedControl
                size="xs"
                value={isDark ? "dark" : "light"}
                onChange={() => toggleColorScheme()}
                data={[
                  { value: "light", label: <Group gap={4} wrap="nowrap"><IconSun size={13} /><Text size="xs">Light</Text></Group> },
                  { value: "dark", label: <Group gap={4} wrap="nowrap"><IconMoon size={13} /><Text size="xs">Dark</Text></Group> },
                ]}
                styles={segStyles}
              />
            }
          />
        </Paper>

        {/* Replay onboarding — its own quiet row. */}
        <Paper radius="lg" withBorder style={{ overflow: "hidden", background: "var(--mantine-color-body)" }}>
          <SettingRow
            icon={<IconSparkles size={18} stroke={1.7} />}
            color="grape"
            title="Replay the guide"
            desc="Walk through the intro tour again."
            control={
              <Button
                variant="light"
                color="grape"
                size="xs"
                radius="md"
                onClick={() => {
                  onClose();
                  onReplayOnboarding();
                }}
              >
                Replay
              </Button>
            }
          />
        </Paper>

        <Group justify="flex-end" gap="sm" mt={2}>
          <Button variant="subtle" color="gray" radius="md" onClick={onClose}>
            Cancel
          </Button>
          <Button color="lavender" radius="md" onClick={saveSettings}>
            Save settings
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
