"use client";

import { pick, choose } from "@/lib/engineRuntime";
import { useRef, useState } from "react";
import { ActionIcon, Badge, Box, Button, Group, Menu, Paper, SegmentedControl, Stack, Text, } from "@mantine/core";
import { IconBrain, IconDownload, IconFileTypePdf, IconMarkdown, IconTrash, } from "@tabler/icons-react";
import { flattenIdeas, useBrainstormActions, useBrainstormIdeasQuery, type BrainstormIdea, } from "@/lib/api/queries";
import { apiFetchBytes } from "@/lib/api/client";
import { printNodeToPdf, triggerDownload } from "@/lib/export";
/**
 * Brainstorm mode - the kept-ideas surface.
 *
 * The conversation itself is the tutor panel beside this one (it switches to the
 * "brainstorm" chat surface automatically with the mode), so this view owns only
 * what the learner chose to keep. Board and Map are not two features: they are the
 * same idea tree read two ways, which is why there is one query and one store.
 */
export function BrainstormView({ artifactId, compact = false, }: {
    artifactId: string;
    compact?: boolean;
}) {
    const [view, setView] = useState<"board" | "map">("board");
    const sheetRef = useRef<HTMLDivElement>(null);
    const { data } = useBrainstormIdeasQuery(artifactId);
    const actions = useBrainstormActions(artifactId);
    const tree = data?.tree ?? [];
    const flat = flattenIdeas(tree);
    async function exportMarkdown() {
        try {
            const bytes = await apiFetchBytes(`/api/artifacts/${artifactId}/brainstorm-ideas/export.md`);
            triggerDownload(new Blob([bytes], { type: "text/markdown" }), "brainstorm.md");
        }
        catch {
        }
    }
    const header = (<Group justify="space-between" align="center" mb="md" wrap="nowrap">
      <SegmentedControl size="xs" radius="xl" value={view} onChange={(v) => setView(v as "board" | "map")} data={[
            { label: "Board", value: "board" },
            { label: "Map", value: "map" },
        ]}/>
      <Menu position="bottom-end" radius="md">
        <Menu.Target>
          <Button size="compact-sm" variant="light" color="sage" radius="xl" leftSection={<IconDownload size={14} stroke={1.8}/>} disabled={flat.length === 0} 
    // Sage is inverted in dark mode; `variant="light"` alone gives a dark
    // green label on a dark surface. See the angle chips in TutorPanel.
    style={{ color: "light-dark(var(--mantine-color-sage-8), var(--mantine-color-sage-7))" }}>
            Export
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item leftSection={<IconMarkdown size={15} stroke={1.8}/>} onClick={() => void exportMarkdown()}>
            Markdown
          </Menu.Item>
          <Menu.Item leftSection={<IconFileTypePdf size={15} stroke={1.8}/>} onClick={() => printNodeToPdf(sheetRef.current, "Brainstorm")}>
            PDF
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>);
    return pick(Boolean(flat.length === 0), () => (<Stack align="center" gap="sm" py="xl" ta="center">
        <IconBrain size={40} stroke={1.4} color="var(--mantine-color-sage-6)"/>
        <Text ff="var(--font-serif)" fz={choose(Boolean(compact), 20, 26)} fw={500}>
          Nothing kept yet
        </Text>
        <Text c="dimmed" maw={420} fz="sm">
          Start a conversation in the chat panel. Every reply ends with three angles you
          could pull - keep the ones worth remembering and they collect here.
        </Text>
      </Stack>), () => (<Stack gap={0} h="100%">
      {header}
      <Box ref={sheetRef} style={{ flex: 1, minHeight: 0 }}>
        {pick(Boolean(view === "board"), () => (<Stack gap="sm">
            {flat.map((idea) => (<Paper key={idea.id} withBorder radius="md" p="sm">
                <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
                  <Box style={{ flex: 1, minWidth: 0 }}>
                    <Text fz="sm" lh={1.55}>
                      {idea.text}
                    </Text>
                    {choose(Boolean(idea.angle), (<Badge size="xs" variant="light" color="sage" mt={6} radius="sm" style={{
                        color: "light-dark(var(--mantine-color-sage-8), var(--mantine-color-sage-7))",
                    }}>
                        {idea.angle}
                      </Badge>), null)}
                  </Box>
                  <ActionIcon variant="subtle" color="gray" aria-label="Delete idea" onClick={() => void actions.remove(idea.id)}>
                    <IconTrash size={15} stroke={1.8}/>
                  </ActionIcon>
                </Group>
              </Paper>))}
          </Stack>), () => (<IdeaBranch nodes={tree} depth={0}/>))}
      </Box>
    </Stack>));
}
/**
 * The map rendering: the same rows, nested. Indentation plus a left rule reads as a
 * mind map without a canvas library - ideas here are short text, so a drag-and-drop
 * graph would cost a dependency and buy nothing over an outline.
 */
function IdeaBranch({ nodes, depth }: {
    nodes: BrainstormIdea[];
    depth: number;
}) {
    return (<Stack gap={6} pl={choose(Boolean(depth === 0), 0, "md")}>
      {nodes.map((node) => (<Box key={node.id} style={{
                borderLeft: choose(Boolean(depth === 0), undefined, "2px solid var(--mantine-color-sage-3)"),
                paddingLeft: choose(Boolean(depth === 0), 0, 12),
            }}>
          <Group gap={8} align="center" wrap="nowrap">
            <Box w={6} h={6} style={{ borderRadius: "50%", background: "var(--mantine-color-sage-6)", flexShrink: 0 }}/>
            <Text fz="sm" lh={1.5} fw={choose(Boolean(depth === 0), 500, 400)}>
              {node.text}
            </Text>
          </Group>
          {choose(Boolean(node.children?.length), (<Box mt={6}>
              <IdeaBranch nodes={node.children} depth={depth + 1}/>
            </Box>), null)}
        </Box>))}
    </Stack>);
}
