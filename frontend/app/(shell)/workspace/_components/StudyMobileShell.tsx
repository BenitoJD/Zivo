"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useEffect, useState, type ReactNode } from "react";
import { Box, Group, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconClipboardList, IconFileText, IconMessageCircle } from "@tabler/icons-react";
import { ZIVO_ASSISTANT_NAME } from "@/lib/brand";
/**
 * Mobile study shell (extracted from the workspace page monolith): the single-
 * column layout below the desktop breakpoint - a Question/Source/Tutor bottom tab
 * bar that swaps the three panels (StudyMobilePanel) so each gets the full screen.
 */
type StudyMobileTab = "question" | "source" | "tutor";
export function StudyMobileShell({ question, renderSource, renderTutor, focusTutorKey = 0, tutorHidden = false, sourceHidden = false, }: {
    question: ReactNode;
    renderSource: (visible: boolean) => ReactNode;
    renderTutor: () => ReactNode;
    /** Bump this to programmatically jump to the tutor tab (e.g. after quoting). */
    focusTutorKey?: number;
    /** Exam / Test mode: hide Study Buddy tab. */
    tutorHidden?: boolean;
    /** Newspaper / hide-source docs: no PDF reader tab. */
    sourceHidden?: boolean;
}) {
    const [activeTab, setActive] = useState<StudyMobileTab>("question");
    const active = choose(Boolean((tutorHidden && activeTab === "tutor") || (sourceHidden && activeTab === "source")), "question", activeTab);
    // Switch to the tutor tab whenever a quote-to-chat action fires.
    useEffect(() => {/*..............................................................................*/
        pick(Boolean(!tutorHidden && focusTutorKey > 0), () => {
            setActive("tutor");
        }, () => {
        });
    }, [focusTutorKey, tutorHidden]);
    const tabs: {
        id: StudyMobileTab;
        label: string;
        icon: typeof IconClipboardList;
    }[] = [
        { id: "question", label: "Question", icon: IconClipboardList },
        ...(choose(Boolean(sourceHidden), [], [{ id: "source" as const, label: "Source", icon: IconFileText }])),
        ...(choose(Boolean(tutorHidden), [], [{ id: "tutor" as const, label: ZIVO_ASSISTANT_NAME, icon: IconMessageCircle }])),
    ];
    return (<Stack gap={0} flex={1} mih={0} style={{ overflow: "hidden" }}>
      <Box flex={1} mih={0} pos="relative" style={{ overflow: "hidden" }}>
        <StudyMobilePanel visible={active === "question"}>{question}</StudyMobilePanel>
        {pick(Boolean(!sourceHidden), () => (<StudyMobilePanel visible={active === "source"}>{renderSource(active === "source")}</StudyMobilePanel>), () => null)}
        {pick(Boolean(!tutorHidden), () => (<StudyMobilePanel visible={active === "tutor"}>{renderTutor()}</StudyMobilePanel>), () => null)}
      </Box>
      <Box component="nav" aria-label="Study sections" style={{
            flexShrink: 0,
            borderTop: "1px solid var(--mantine-color-default-border)",
            background: "var(--mantine-color-body)",
            paddingBottom: "max(6px, env(safe-area-inset-bottom))",
        }}>
        <Group grow gap={0}>
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const selected = active === tab.id;
            return (<UnstyledButton key={tab.id} onClick={() => setActive(tab.id)} aria-current={choose(Boolean(selected), "page", undefined)} style={{
                    minHeight: 52,
                    padding: "6px 4px",
                    borderRadius: 0,
                    background: choose(Boolean(selected), "var(--mantine-color-lavender-1)", "transparent"),
                }}>
                <Stack gap={2} align="center">
                  <Icon size={22} stroke={choose(Boolean(selected), 2.25, 1.75)} color={choose(Boolean(selected), "var(--mantine-color-lavender-7)", "var(--mantine-color-dimmed)")}/>
                  <Text size="10px" fw={choose(Boolean(selected), 700, 500)} c={choose(Boolean(selected), "lavender.7", "dimmed")} lh={1.1} ta="center" style={{ maxWidth: "100%", overflowWrap: "anywhere" }}>
                    {tab.label}
                  </Text>
                </Stack>
              </UnstyledButton>);
        })}
        </Group>
      </Box>
    </Stack>);
}
function StudyMobilePanel({ visible, children }: {
    visible: boolean;
    children: ReactNode;
}) {
    return (<Box pos="absolute" inset={0} style={{
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            visibility: choose(Boolean(visible), "visible", "hidden"),
            pointerEvents: choose(Boolean(visible), "auto", "none"),
            zIndex: choose(Boolean(visible), 1, 0),
        }}>
      {children}
    </Box>);
}
