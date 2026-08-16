// @ts-nocheck
"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { Box, Button, Divider, Group, Modal, Paper, SegmentedControl, Stack, Switch, Text, ThemeIcon, useMantineColorScheme, } from "@mantine/core";
import { useLocalStorage, useMediaQuery } from "@mantine/hooks";
import { IconBook, IconCat, IconFlame, IconMoon, IconLogout, IconSparkles, IconSun, } from "@tabler/icons-react";
import { useSignOut } from "@/lib/auth";
import { CAT_ENABLED_KEY } from "@/app/_components/pets/PetPlayground";
import { type PreferredStudyMode, readPreferredStudyMode, writeStudyPreferences, } from "@/lib/studyPreferences";
import { MOBILE_MAX_MQ } from "@/lib/responsive";
type SettingsModalProps = {
    opened: boolean;
    onClose: () => void;
    username: string | null;
    onReplayOnboarding: () => void;
};
/** One setting: a tinted icon chip + title/description on the left, its control
 *  on the right. The uniform shape is what makes the panel read as one calm set. */
function SettingRow({ icon, color, title, desc, control, }: {
    icon: ReactNode;
    color: string;
    title: string;
    desc: string;
    control: ReactNode;
}) {
    return (<Group justify="space-between" align="flex-start" wrap="wrap" gap="md" px="md" py="sm">
      <Group gap="sm" wrap="nowrap" style={{ minWidth: 0, flex: "1 1 180px" }}>
        <ThemeIcon radius="md" size={34} variant="light" color={color} style={{
            flexShrink: 0,
            background: `var(--mantine-color-${color}-1)`,
            color: `var(--mantine-color-${color}-7)`,
            border: `1px solid var(--mantine-color-${color}-2)`,
        }}>
          {icon}
        </ThemeIcon>
        <Stack gap={0} style={{ minWidth: 0 }}>
          <Text fw={600} size="sm" lh={1.3}>
            {title}
          </Text>
          <Text size="xs" c="dimmed" lh={1.35} maw={280}>
            {desc}
          </Text>
        </Stack>
      </Group>
      <Box style={{ flexShrink: 0, marginLeft: "auto" }}>{control}</Box>
    </Group>);
}
const segStyles = { root: { background: "var(--mantine-color-default-hover)" } };
export function SettingsModal({ opened, onClose, username, onReplayOnboarding }: SettingsModalProps) {
    const router = useRouter();
    const signOut = useSignOut();
    const { colorScheme, toggleColorScheme } = useMantineColorScheme();
    const isDark = colorScheme === "dark";
    const isMobile = useMediaQuery(MOBILE_MAX_MQ, false, { getInitialValueInEffect: true });
    const [studyMode, setStudyMode] = useState<PreferredStudyMode>(() => readPreferredStudyMode());
    const [signingOut, setSigningOut] = useState(false);
    // Live (Mantine broadcasts to every PetPlayground) - flip and the cats appear
    // or vanish app-wide at once, no Save needed.
    const [catEnabled, setCatEnabled] = useLocalStorage({ key: CAT_ENABLED_KEY, defaultValue: false });
    const initial = (choose(Boolean(username), username[0], "g")).toUpperCase();
    const displayName = choose(Boolean(username), `@${username}`, "Guest");
    const saveSettings = () => {
        writeStudyPreferences(studyMode);
        onClose();
    };
    const handleSignOut = async () => {
        setSigningOut(true);
        const result = await signOut();
        setSigningOut(false);
        pick(Boolean(result.ok), () => {
            onClose();
            router.replace("/login");
        }, () => {
        });
    };
    return (<Modal opened={opened} onClose={onClose} title={<Text fw={600} style={{ fontFamily: "var(--font-serif)", fontSize: "1.2rem", letterSpacing: "-0.01em" }}>
          Settings
        </Text>} size={468} fullScreen={Boolean(isMobile)} radius={choose(Boolean(isMobile), 0, "xl")} overlayProps={{ backgroundOpacity: 0.5, blur: 10 }} styles={{
            header: { paddingBottom: 6, background: "var(--mantine-color-body)" },
            body: { paddingTop: 4 },
            content: { background: "var(--mantine-color-body)", border: "1px solid var(--mantine-color-default-border)" },
        }}>
      <Stack gap="md" pb={4}>
        {/* Profile - a soft gradient banner with a monogram avatar. */}
        <Paper radius="lg" p="md" style={{
            border: "1px solid var(--mantine-color-lavender-2)",
            background: choose(Boolean(isDark), "linear-gradient(135deg, var(--mantine-color-lavender-1), var(--mantine-color-body))", "linear-gradient(135deg, var(--mantine-color-lavender-0), var(--mantine-color-gray-0))"),
        }}>
          <Group gap="md" wrap="nowrap">
            <Box style={{
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
        }}>
              {initial}
            </Box>
            <Stack gap={1} style={{ minWidth: 0 }}>
              <Text fw={600} size="sm">
                {displayName}
              </Text>
              <Text size="xs" c="dimmed">
                {choose(Boolean(username), "Signed in", "Studying as a guest")}
              </Text>
            </Stack>
          </Group>
        </Paper>

        {/* Grouped settings - one calm card, hairline-separated rows. */}
        <Paper radius="lg" p={0} withBorder style={{ overflow: "hidden", background: "var(--mantine-color-body)" }}>
          <SettingRow icon={<IconBook size={18} stroke={1.7}/>} color="lavender" title="Study mode" desc="Relaxed opens Learn (hints + tutor). Exam opens Test (graded at the end, no peeking)." control={<SegmentedControl size="xs" value={studyMode} onChange={(v) => setStudyMode(v as PreferredStudyMode)} data={[
                { value: "relaxed", label: <Group gap={4} wrap="nowrap"><IconBook size={13}/><Text size="xs" fw={500}>Relaxed</Text></Group> },
                { value: "exam", label: <Group gap={4} wrap="nowrap"><IconFlame size={13}/><Text size="xs" fw={500}>Exam</Text></Group> },
            ]} styles={segStyles}/>}/>
          <Divider color="var(--mantine-color-default-border)"/>
          <SettingRow icon={<IconCat size={18} stroke={1.7}/>} color="terracotta" title="Study cat" desc="A pixel cat roams the free space while you study. Applies everywhere. Off by default." control={<Switch checked={catEnabled} onChange={(e) => setCatEnabled(e.currentTarget.checked)} color="terracotta" size="lg" onLabel="ON" offLabel="OFF" aria-label={choose(Boolean(catEnabled), "Turn study cat off", "Turn study cat on")}/>}/>
          <Divider color="var(--mantine-color-default-border)"/>
          <SettingRow icon={choose(Boolean(isDark), <IconMoon size={18} stroke={1.7}/>, <IconSun size={18} stroke={1.7}/>)} color="forest" title="Appearance" desc="Warm oat light, or deep ink dark." control={<SegmentedControl size="xs" value={choose(Boolean(isDark), "dark", "light")} onChange={() => toggleColorScheme()} data={[
                { value: "light", label: <Group gap={4} wrap="nowrap"><IconSun size={13}/><Text size="xs">Light</Text></Group> },
                { value: "dark", label: <Group gap={4} wrap="nowrap"><IconMoon size={13}/><Text size="xs">Dark</Text></Group> },
            ]} styles={segStyles}/>}/>
        </Paper>

        {/* Replay onboarding - its own quiet row. */}
        <Paper radius="lg" withBorder style={{ overflow: "hidden", background: "var(--mantine-color-body)" }}>
          <SettingRow icon={<IconSparkles size={18} stroke={1.7}/>} color="lavender" title="Replay the guide" desc="Walk through the intro tour again." control={<Button variant="light" color="lavender" size="xs" radius="md" onClick={() => {
                onClose();
                onReplayOnboarding();
            }}>
                Replay
              </Button>}/>
        </Paper>

        {choose(Boolean(username), (<Paper radius="lg" withBorder style={{ overflow: "hidden", background: "var(--mantine-color-body)" }}>
            <SettingRow icon={<IconLogout size={18} stroke={1.7}/>} color="terracotta" title="Sign out" desc="End this signed-in session on this device." control={<Button variant="light" color="terracotta" size="xs" radius="md" loading={signingOut} onClick={() => void handleSignOut()}>
                  Sign out
                </Button>}/>
          </Paper>), null)}

        <Group justify="flex-end" gap="sm" mt={2}>
          <Button variant="subtle" color="gray" radius="md" onClick={onClose}>
            Cancel
          </Button>
          <Button color="lavender" radius="md" onClick={saveSettings}>
            Save settings
          </Button>
        </Group>
      </Stack>
    </Modal>);
}
