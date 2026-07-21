"use client";

import { useState } from "react";
import {
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
  useMantineColorScheme,
} from "@mantine/core";
import { useLocalStorage } from "@mantine/hooks";
import {
  IconBook,
  IconCat,
  IconFlame,
  IconHelp,
  IconMoon,
  IconSettings,
  IconSun,
  IconUser,
} from "@tabler/icons-react";

/** Shared key for the roaming study-cat preference (off by default). */
export const CAT_ENABLED_KEY = "zivo-cat-enabled";

type SettingsModalProps = {
  opened: boolean;
  onClose: () => void;
  username: string | null;
  onReplayOnboarding: () => void;
};

export function SettingsModal({
  opened,
  onClose,
  username,
  onReplayOnboarding,
}: SettingsModalProps) {
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";

  // State linked to LocalStorage overrides
  const [studyMode, setStudyMode] = useState<"relaxed" | "exam">(() => {
    if (typeof window === "undefined") return "relaxed";
    return (localStorage.getItem("zivo-study-mode") as "relaxed" | "exam") || "relaxed";
  });
  const [mcqCount, setMcqCount] = useState<number>(() => {
    if (typeof window === "undefined") return 5;
    const storedCount = localStorage.getItem("zivo-mcq-count");
    return storedCount ? (parseInt(storedCount, 10) || 5) : 5;
  });
  // Applies live (Mantine useLocalStorage broadcasts to the cat's hook), so this
  // one doesn't wait for Save — flip and the cat appears/disappears at once.
  const [catEnabled, setCatEnabled] = useLocalStorage({ key: CAT_ENABLED_KEY, defaultValue: false });

  const displayName = username ? `@${username}` : "@guest";

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
        <Group gap="xs">
          <IconSettings size={20} stroke={1.6} style={{ color: "var(--mantine-color-lavender-7)" }} />
          <Text fw={600} style={{ fontFamily: "var(--font-serif)", fontSize: "1.15rem" }}>
            Preferences & Settings
          </Text>
        </Group>
      }
      size="md"
      overlayProps={{
        backgroundOpacity: 0.45,
        blur: 8,
      }}
      styles={{
        header: {
          borderBottom: "1px solid var(--mantine-color-default-border)",
          paddingBottom: "var(--mantine-spacing-md)",
        },
        content: {
          borderRadius: "var(--mantine-radius-xl)",
          background: "var(--mantine-color-body)",
          border: "1px solid var(--mantine-color-default-border)",
        },
      }}
    >
      <Stack gap="lg" mt="md">
        {/* Profile Card */}
        <Paper withBorder p="md" radius="lg" bg="gray.0">
          <Group gap="md">
            <Paper
              radius="xl"
              style={{
                width: 44,
                height: 44,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "var(--mantine-color-lavender-0)",
                color: "var(--mantine-color-lavender-7)",
                border: "1px solid var(--mantine-color-lavender-2)",
              }}
            >
              <IconUser size={22} stroke={1.5} />
            </Paper>
            <Stack gap={2} style={{ flex: 1 }}>
              <Text fw={600} size="sm">
                Study Profile
              </Text>
              <Text size="xs" c="gray.6">
                Logged in as {displayName}
              </Text>
            </Stack>
          </Group>
        </Paper>

        {/* Study Mode Choice */}
        <Stack gap="xs">
          <Group justify="space-between" align="flex-start" wrap="nowrap">
            <Stack gap={2}>
              <Text fw={600} size="sm">
                Study Mode
              </Text>
              <Text size="xs" c="gray.6" maw={260}>
                Relaxed provides unlimited hints. Exam runs timed with no tutor advice.
              </Text>
            </Stack>
            <SegmentedControl
              value={studyMode}
              onChange={(v) => setStudyMode(v as "relaxed" | "exam")}
              data={[
                {
                  value: "relaxed",
                  label: (
                    <Group gap={4} wrap="nowrap">
                      <IconBook size={14} />
                      <Text size="xs" fw={500}>Relaxed</Text>
                    </Group>
                  ),
                },
                {
                  value: "exam",
                  label: (
                    <Group gap={4} wrap="nowrap">
                      <IconFlame size={14} />
                      <Text size="xs" fw={500}>Exam</Text>
                    </Group>
                  ),
                },
              ]}
              styles={{
                root: {
                  background: "var(--mantine-color-default-hover)",
                },
              }}
            />
          </Group>
        </Stack>

        <Divider color="gray.3" />

        {/* Study companion (roaming cat) — off by default */}
        <Group justify="space-between" align="center" wrap="nowrap">
          <Group gap="sm" wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
            <IconCat size={20} stroke={1.6} style={{ flexShrink: 0, color: "var(--mantine-color-lavender-6)" }} />
            <Stack gap={2}>
              <Text fw={600} size="sm">
                Study cat
              </Text>
              <Text size="xs" c="gray.6" maw={240}>
                A little cat roams the empty space while you study. Off by default.
              </Text>
            </Stack>
          </Group>
          <Switch
            checked={catEnabled}
            onChange={(e) => setCatEnabled(e.currentTarget.checked)}
            color="lavender"
            size="md"
            aria-label={catEnabled ? "Turn study cat off" : "Turn study cat on"}
          />
        </Group>

        <Divider color="gray.3" />

        {/* Question Generation Scale */}
        <Group justify="space-between" align="center" wrap="nowrap">
          <Stack gap={2}>
            <Text fw={600} size="sm">
              Default MCQ Count
            </Text>
            <Text size="xs" c="gray.6">
              Number of questions to generate for new source uploads.
            </Text>
          </Stack>
          <NumberInput
            value={mcqCount}
            onChange={(v) => setMcqCount(typeof v === "number" ? v : 5)}
            min={3}
            max={30}
            step={1}
            w={80}
            size="sm"
            styles={{
              input: {
                textAlign: "center",
              },
            }}
          />
        </Group>

        <Divider color="gray.3" />

        {/* Appearance Settings */}
        <Group justify="space-between" align="center">
          <Stack gap={2}>
            <Text fw={600} size="sm">
              Appearance
            </Text>
            <Text size="xs" c="gray.6">
              Toggle light oat or dark ink themes.
            </Text>
          </Stack>
          <SegmentedControl
            value={isDark ? "dark" : "light"}
            onChange={() => toggleColorScheme()}
            data={[
              {
                value: "light",
                label: (
                  <Group gap={4} wrap="nowrap">
                    <IconSun size={14} />
                    <Text size="xs">Light</Text>
                  </Group>
                ),
              },
              {
                value: "dark",
                label: (
                  <Group gap={4} wrap="nowrap">
                    <IconMoon size={14} />
                    <Text size="xs">Dark</Text>
                  </Group>
                ),
              },
            ]}
          />
        </Group>

        <Divider color="gray.3" />

        {/* Replay Onboarding */}
        <Group justify="space-between" align="center">
          <Stack gap={2}>
            <Text fw={600} size="sm">
              Need help?
            </Text>
            <Text size="xs" c="gray.6">
              Review the initial step-by-step onboarding presentation.
            </Text>
          </Stack>
          <Button
            variant="subtle"
            color="gray"
            size="sm"
            leftSection={<IconHelp size={16} />}
            onClick={() => {
              onClose();
              onReplayOnboarding();
            }}
          >
            Replay Guide
          </Button>
        </Group>

        {/* Actions */}
        <Group justify="flex-end" gap="sm" mt="md">
          <Button variant="subtle" color="gray" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={saveSettings} color="lavender">
            Save settings
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
