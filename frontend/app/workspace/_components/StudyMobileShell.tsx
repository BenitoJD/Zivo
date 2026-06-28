"use client";

import { useState, type ReactNode } from "react";
import { Box, Group, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconClipboardList, IconFileText, IconMessageCircle } from "@tabler/icons-react";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";

/**
 * Mobile study shell (extracted from the workspace page monolith): the single-
 * column layout below the desktop breakpoint — a Question/Source/Tutor bottom tab
 * bar that swaps the three panels (StudyMobilePanel) so each gets the full screen.
 */
type StudyMobileTab = "question" | "source" | "tutor";

export function StudyMobileShell({
  question,
  renderSource,
  renderTutor,
}: {
  question: ReactNode;
  renderSource: (visible: boolean) => ReactNode;
  renderTutor: () => ReactNode;
}) {
  const [active, setActive] = useState<StudyMobileTab>("question");

  const tabs: { id: StudyMobileTab; label: string; icon: typeof IconClipboardList }[] = [
    { id: "question", label: "Question", icon: IconClipboardList },
    { id: "source", label: "Source", icon: IconFileText },
    { id: "tutor", label: ZIVO_ASSISTANT_NAME, icon: IconMessageCircle },
  ];

  return (
    <Stack gap={0} flex={1} mih={0} style={{ overflow: "hidden" }}>
      <Box flex={1} mih={0} pos="relative" style={{ overflow: "hidden" }}>
        <StudyMobilePanel visible={active === "question"}>{question}</StudyMobilePanel>
        <StudyMobilePanel visible={active === "source"}>{renderSource(active === "source")}</StudyMobilePanel>
        <StudyMobilePanel visible={active === "tutor"}>{renderTutor()}</StudyMobilePanel>
      </Box>
      <Box
        component="nav"
        aria-label="Study sections"
        style={{
          flexShrink: 0,
          borderTop: "1px solid var(--mantine-color-default-border)",
          background: "var(--mantine-color-body)",
          paddingBottom: "max(6px, env(safe-area-inset-bottom))",
        }}
      >
        <Group grow gap={0}>
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const selected = active === tab.id;
            return (
              <UnstyledButton
                key={tab.id}
                onClick={() => setActive(tab.id)}
                aria-current={selected ? "page" : undefined}
                style={{
                  minHeight: 52,
                  padding: "6px 4px",
                  borderRadius: 0,
                  background: selected ? "var(--mantine-color-lavender-1)" : "transparent",
                }}
              >
                <Stack gap={2} align="center">
                  <Icon
                    size={22}
                    stroke={selected ? 2.25 : 1.75}
                    color={selected ? "var(--mantine-color-lavender-7)" : "var(--mantine-color-dimmed)"}
                  />
                  <Text size="10px" fw={selected ? 700 : 500} c={selected ? "lavender.7" : "dimmed"} lh={1.1}>
                    {tab.label}
                  </Text>
                </Stack>
              </UnstyledButton>
            );
          })}
        </Group>
      </Box>
    </Stack>
  );
}

function StudyMobilePanel({ visible, children }: { visible: boolean; children: ReactNode }) {
  return (
    <Box
      pos="absolute"
      inset={0}
      style={{
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        visibility: visible ? "visible" : "hidden",
        pointerEvents: visible ? "auto" : "none",
        zIndex: visible ? 1 : 0,
      }}
    >
      {children}
    </Box>
  );
}
