"use client";

import {
  ActionIcon,
  Box,
  Button,
  Center,
  Group,
  NavLink,
  Progress,
  ScrollArea,
  SegmentedControl,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
  useMantineColorScheme,
} from "@mantine/core";
import {
  IconBook2,
  IconBriefcase,
  IconBuildingCastle,
  IconBulb,
  IconFileCv,
  IconCards,
  IconClipboardList,
  IconCpu,
  IconFileText,
  IconLayoutSidebarLeftCollapse,
  IconListCheck,
  IconLogin,
  IconMessage2,
  IconMoon,
  IconNotebook,
  IconSettings,
  IconSun,
  IconTrash,
  IconUpload,
} from "@tabler/icons-react";
import { BrandMark } from "@/app/_components/BrandMark";
import type { SourceDocument } from "@/lib/types";
import { useStudyNav, type StudyMode } from "@/app/workspace/_components/studyNav";

/** Mode navigator content shared by the mini + expanded sidebar (below the sources). */
const MODE_GROUPS: { heading: string; items: { value: StudyMode; label: string; icon: typeof IconBook2 }[] }[] = [
  {
    heading: "Study",
    items: [
      { value: "read", label: "Read", icon: IconBook2 },
      { value: "learn", label: "Learn", icon: IconBulb },
      { value: "test", label: "Test", icon: IconClipboardList },
    ],
  },
  {
    heading: "Tools",
    items: [
      { value: "explain", label: "Explain", icon: IconMessage2 },
      { value: "notes", label: "Notes", icon: IconNotebook },
      { value: "cards", label: "Cards", icon: IconCards },
      { value: "palace", label: "Palace", icon: IconBuildingCastle },
      { value: "quiz", label: "Quiz", icon: IconListCheck },
      { value: "interview", label: "Interview", icon: IconBriefcase },
      { value: "resume", label: "Resume", icon: IconFileCv },
    ],
  },
];

export const SIDEBAR_MINI_WIDTH = 64;
export const SIDEBAR_EXPANDED_WIDTH = 280;
export const SHELL_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";
// A touch longer than a snap — the rail glides open and pushes the page with it,
// settling on the brand ease for a buttery, Apple-like expand.
export const SHELL_MS = 340;
const MINI_RAIL_ICON_SIZE = 42;

function SidebarAnimatedLayer({
  visible,
  children,
  enterDelay = 0,
  reduceMotion,
}: {
  visible: boolean;
  children: React.ReactNode;
  enterDelay?: number;
  reduceMotion: boolean;
}) {
  const duration = reduceMotion ? 0 : SHELL_MS;
  const delay = reduceMotion ? 0 : enterDelay;
  const closeMs = reduceMotion ? 0 : Math.round(duration * 0.45);
  return (
    <Box
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        overflow: "hidden",
        opacity: visible ? 1 : 0,
        transition: visible
          ? `opacity ${duration}ms ${SHELL_EASE} ${delay}ms`
          : `opacity ${closeMs}ms ease-in`,
        pointerEvents: visible ? "auto" : "none",
        zIndex: visible ? 2 : 1,
      }}
      aria-hidden={!visible}
    >
      {children}
    </Box>
  );
}

function MiniRailButton({
  label,
  onClick,
  active = false,
  emphasized = false,
  disabled = false,
  children,
}: {
  label: string;
  onClick?: () => void;
  active?: boolean;
  emphasized?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  const button = (
    <Box
      component="button"
      type="button"
      disabled={disabled}
      onClick={onClick}
      w={MINI_RAIL_ICON_SIZE}
      h={MINI_RAIL_ICON_SIZE}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        border: emphasized ? "1px solid var(--mantine-color-default-border)" : "none",
        borderRadius: "var(--mantine-radius-md)",
        background: active
          ? "var(--mantine-color-lavender-1)"
          : emphasized
            ? "var(--mantine-color-default)"
            : "transparent",
        cursor: disabled ? "default" : "pointer",
        padding: 0,
        color: active ? "var(--mantine-color-lavender-7)" : "var(--mantine-color-dimmed)",
        opacity: disabled ? 0.45 : 1,
      }}
      aria-label={label}
    >
      {children}
    </Box>
  );

  if (disabled) return button;
  return (
    <Tooltip label={label} position="right" withArrow>
      {button}
    </Tooltip>
  );
}

/** Mini (collapsed) mode navigator — a column of mode icons under the source icons. */
function MiniModeNav({ mode, onChange }: { mode: StudyMode; onChange: (m: StudyMode) => void }) {
  return (
    <Stack gap={6} align="center" w="100%" pt={6} mt={2} style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
      {MODE_GROUPS.flatMap((g) => g.items).map((it) => (
        <MiniRailButton key={it.value} label={it.label} active={mode === it.value} onClick={() => onChange(it.value)}>
          <it.icon size={18} stroke={1.6} />
        </MiniRailButton>
      ))}
    </Stack>
  );
}

/** Expanded mode navigator — grouped, labelled rows, placed below the source list. */
function ExpandedModeNav({ mode, onChange }: { mode: StudyMode; onChange: (m: StudyMode) => void }) {
  return (
    <Box mt="sm" pt="sm" style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
      <style>{`
        .zv-mode-row {
          display: flex; align-items: center; gap: 10px; width: 100%;
          padding: 8px 10px; border-radius: 11px; color: var(--mantine-color-dimmed);
          transition: background 150ms ease, color 150ms ease;
        }
        .zv-mode-row:hover { background: var(--mantine-color-default-hover); color: var(--mantine-color-text); }
        .zv-mode-row[data-active="true"] { background: var(--mantine-color-lavender-0); color: var(--mantine-color-lavender-7); font-weight: 600; }
        [data-mantine-color-scheme="dark"] .zv-mode-row[data-active="true"] { background: var(--mantine-color-lavender-2); color: var(--mantine-color-lavender-9); }
        @media (prefers-reduced-motion: reduce) { .zv-mode-row { transition: none !important; } }
      `}</style>
      <Stack gap="sm">
        {MODE_GROUPS.map((g) => (
          <Stack key={g.heading} gap={3}>
            <Text size="xs" tt="uppercase" fw={700} c="dimmed" lts={1.4} px="sm" mb={2} style={{ fontSize: 11 }}>
              {g.heading}
            </Text>
            {g.items.map((it) => {
              const active = mode === it.value;
              return (
                <UnstyledButton
                  key={it.value}
                  className="zv-mode-row"
                  data-active={active}
                  onClick={() => onChange(it.value)}
                  aria-current={active ? "page" : undefined}
                >
                  <it.icon size={18} stroke={active ? 2 : 1.7} style={{ flexShrink: 0 }} />
                  <Text size="sm" style={{ fontWeight: "inherit" }}>{it.label}</Text>
                </UnstyledButton>
              );
            })}
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}

export function sourceLabel(filename: string) {
  return filename.replace(/\.[^.]+$/, "");
}

export function sourceDescription(status: SourceDocument["status"], progress: number) {
  if (status === "indexing") return `Indexing ${progress}%`;
  if (status === "pending") return "Choose pages";
  return status;
}

/** Quiet status signal — a colored dot + short label, instead of raw status text. */
function sourceStatusMeta(status: SourceDocument["status"], progress: number) {
  if (status === "indexing")
    return { dot: "var(--mantine-color-lavender-5)", label: `Indexing ${progress}%`, pulse: true };
  if (status === "pending")
    return { dot: "var(--mantine-color-gray-5)", label: "Choose pages", pulse: false };
  if (status === "ready" || status === "indexed")
    return { dot: "var(--mantine-color-sage-5)", label: "Ready", pulse: false };
  return { dot: "var(--mantine-color-gray-5)", label: status, pulse: false };
}

/**
 * A single source in the expanded rail — Wispr-calm: a tight, single-line row with
 * a tinted glyph chip, a truncated title, and a quiet status dot. The active row
 * lifts onto a soft lavender surface; the delete affordance stays hidden until hover.
 */
function SourceRow({
  doc,
  active,
  onNavigate,
  onDelete,
}: {
  doc: SourceDocument;
  active: boolean;
  onNavigate: () => void;
  onDelete: () => void;
}) {
  const label = sourceLabel(doc.filename);
  const meta = sourceStatusMeta(doc.status, doc.index_progress);
  return (
    <UnstyledButton
      className="zivo-source-row"
      data-active={active || undefined}
      onClick={onNavigate}
      aria-label={label}
    >
      <span className="zivo-source-chip" aria-hidden>
        <IconFileText size={16} stroke={1.7} />
      </span>
      <span className="zivo-source-body">
        <Text size="sm" fw={500} truncate className="zivo-source-title">
          {label}
        </Text>
        <span className="zivo-source-status">
          <span
            className={meta.pulse ? "zivo-source-dot zivo-source-dot-pulse" : "zivo-source-dot"}
            style={{ background: meta.dot }}
          />
          <Text size="xs" c="dimmed" truncate>
            {meta.label}
          </Text>
        </span>
      </span>
      <Tooltip label="Delete source" position="right" withArrow openDelay={300}>
        <span
          role="button"
          tabIndex={-1}
          aria-label={`Delete ${label}`}
          className="zivo-source-del"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onDelete();
          }}
        >
          <IconTrash size={15} stroke={1.7} />
        </span>
      </Tooltip>
    </UnstyledButton>
  );
}

export type SidebarProps = {
  /** Source list. */
  documents: SourceDocument[];
  /** Currently-viewed artifact id (for active highlight). */
  artifactId?: string;
  /** Whether the expanded (wide) view is showing. */
  wide: boolean;
  /** Whether this is the mobile drawer (affects padding/scroll). */
  isMobile: boolean;
  reduceMotion: boolean;
  username: string | null;
  isAdmin: boolean;
  storagePct: number;
  pathname: string;
  onToggleSidebar: () => void;
  onNavigateSource: (id: string) => void;
  onAddSource: () => void;
  onSignIn: () => void;
  onOpenModels: () => void;
  onDeleteSource: (doc: SourceDocument) => void;
  onOpenSettings: () => void;
};

export function Sidebar({
  documents,
  artifactId,
  wide,
  isMobile,
  reduceMotion,
  username,
  isAdmin,
  storagePct,
  pathname,
  onToggleSidebar,
  onNavigateSource,
  onAddSource,
  onSignIn,
  onOpenModels,
  onDeleteSource,
  onOpenSettings,
}: SidebarProps) {
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";
  // When an artifact study view is mounted, host its mode navigator here (below
  // the sources) rather than as a separate rail.
  const { active: studyActive, mode: studyMode, setMode: setStudyMode } = useStudyNav();

  return (
    <>
      <style>{`
        /* Source row — Wispr-calm: tight, single line, tinted glyph chip, hover-reveal delete. */
        .zivo-source-row {
          position: relative;
          width: 100%;
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 8px 10px;
          border-radius: 12px;
          border: 1px solid transparent;
          transition: background 140ms ease, border-color 140ms ease;
        }
        .zivo-source-row:hover { background: var(--mantine-color-default-hover); }
        .zivo-source-row[data-active] {
          background: var(--mantine-color-lavender-0);
          border-color: var(--mantine-color-lavender-2);
        }
        .zivo-source-chip {
          flex-shrink: 0;
          width: 32px;
          height: 32px;
          border-radius: 9px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--mantine-color-default-hover);
          color: var(--mantine-color-dimmed);
          transition: background 140ms ease, color 140ms ease;
        }
        .zivo-source-row:hover .zivo-source-chip { background: var(--mantine-color-gray-2); }
        .zivo-source-row[data-active] .zivo-source-chip {
          background: var(--mantine-color-lavender-1);
          color: var(--mantine-color-lavender-7);
        }
        .zivo-source-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; text-align: left; }
        .zivo-source-row[data-active] .zivo-source-title { color: var(--mantine-color-lavender-8); }
        .zivo-source-status { display: flex; align-items: center; gap: 6px; min-width: 0; }
        .zivo-source-dot { flex-shrink: 0; width: 6px; height: 6px; border-radius: 999px; }
        .zivo-source-dot-pulse { animation: zivo-dot-pulse 1.6s ease-in-out infinite; }
        @keyframes zivo-dot-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
        .zivo-source-del {
          position: absolute;
          right: 8px;
          top: 50%;
          transform: translateY(-50%);
          width: 28px;
          height: 28px;
          border-radius: 8px;
          display: flex;
          align-items: center;
          justify-content: center;
          color: var(--mantine-color-dimmed);
          background: var(--mantine-color-gray-2);
          opacity: 0;
          transition: opacity 120ms ease, background 140ms ease, color 140ms ease;
          cursor: pointer;
        }
        .zivo-source-row:hover .zivo-source-del { opacity: 1; }
        .zivo-source-del:hover { background: var(--mantine-color-terracotta-0); color: var(--mantine-color-terracotta-6); }

        /* Add source — Wispr soft lavender pill (ink text), not a heavy saturated fill. */
        .zivo-add-source {
          background: #EFDBFB !important;
          color: var(--mantine-color-text) !important;
          border: 1px solid #E1C9F5 !important;
          box-shadow: none !important;
          font-weight: 600 !important;
          transition: background 160ms ease, transform 140ms ease, border-color 160ms ease !important;
        }
        .zivo-add-source:hover { background: #E8D0F8 !important; transform: translateY(-1px); }
        .zivo-add-source:active { transform: translateY(0); }
        [data-mantine-color-scheme="dark"] .zivo-add-source {
          background: var(--mantine-color-lavender-2) !important;
          border-color: var(--mantine-color-lavender-3) !important;
          color: var(--mantine-color-lavender-9) !important;
        }
        [data-mantine-color-scheme="dark"] .zivo-add-source:hover { background: var(--mantine-color-lavender-3) !important; }
        @media (prefers-reduced-motion: reduce) {
          .zivo-add-source, .zivo-source-row, .zivo-source-chip, .zivo-source-del { transition: none !important; }
          .zivo-source-dot-pulse { animation: none !important; }
        }
      `}</style>
      {/* Header row (desktop only) */}
      {!isMobile && (
        <Box pos="relative" h={52} w="100%">
          <SidebarAnimatedLayer visible={!wide} reduceMotion={reduceMotion}>
            <Center h={52}>
              <Tooltip label="Expand sidebar" position="right" withArrow>
                <UnstyledButton onClick={onToggleSidebar} aria-label="Expand sidebar" p={4}>
                  <BrandMark showWord={false} height={32} />
                </UnstyledButton>
              </Tooltip>
            </Center>
          </SidebarAnimatedLayer>
          <SidebarAnimatedLayer visible={wide} enterDelay={60} reduceMotion={reduceMotion}>
            <Group px="md" h={52} justify="space-between" wrap="nowrap" gap="sm">
              <Group gap="sm" wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
                <BrandMark showWord={true} height={30} />
              </Group>
              <ActionIcon
                variant="subtle"
                color="gray"
                onClick={onToggleSidebar}
                aria-label="Collapse sidebar"
                style={{ flexShrink: 0 }}
              >
                <IconLayoutSidebarLeftCollapse size={18} stroke={1.5} />
              </ActionIcon>
            </Group>
          </SidebarAnimatedLayer>
        </Box>
      )}

      {/* Source list */}
      <Box style={{ flex: 1, minHeight: 0, overflow: "hidden", position: "relative" }}>
        <SidebarAnimatedLayer visible={!wide} reduceMotion={reduceMotion}>
          <Box h="100%" w="100%" style={{ overflowY: "auto", overflowX: "hidden" }}>
            <Stack gap={6} align="center" w="100%" py={4}>
              {documents.length === 0 ? (
                <MiniRailButton label="No sources yet" disabled>
                  <IconFileText size={18} stroke={1.5} />
                </MiniRailButton>
              ) : (
                documents.map((d) => {
                  const lbl = sourceLabel(d.filename);
                  return (
                    <MiniRailButton
                      key={d.id}
                      label={lbl}
                      active={artifactId === d.id}
                      onClick={() => onNavigateSource(d.id)}
                    >
                      <IconFileText size={18} stroke={1.5} />
                    </MiniRailButton>
                  );
                })
              )}
            </Stack>
            {studyActive && <MiniModeNav mode={studyMode} onChange={setStudyMode} />}
          </Box>
        </SidebarAnimatedLayer>

        <SidebarAnimatedLayer visible={wide} enterDelay={80} reduceMotion={reduceMotion}>
          <ScrollArea
            h="100%"
            type={isMobile ? "never" : "auto"}
            scrollbars="y"
            offsetScrollbars={!isMobile}
            px="xs"
            pt={isMobile ? "md" : "xs"}
          >
            <Group justify="space-between" align="center" px="sm" mb={8} mt={2}>
              <Text size="xs" tt="uppercase" fw={700} c="dimmed" lts={1.4} style={{ fontSize: 11 }}>
                Sources
              </Text>
              {documents.length > 0 && (
                <Text size="xs" c="dimmed" fw={600} style={{ fontVariantNumeric: "tabular-nums", opacity: 0.7 }}>
                  {documents.length}
                </Text>
              )}
            </Group>
            {documents.length === 0 ? (
              <Stack align="center" gap={6} px="md" py="lg" ta="center">
                <Box
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: 12,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: "var(--mantine-color-default-hover)",
                    color: "var(--mantine-color-dimmed)",
                  }}
                >
                  <IconFileText size={20} stroke={1.5} />
                </Box>
                <Text size="sm" c="dimmed" lh={1.5} maw={200}>
                  No sources yet. Add a PDF, doc, or notes to start studying.
                </Text>
              </Stack>
            ) : (
              <Stack gap={2}>
                {documents.map((d) => (
                  <SourceRow
                    key={d.id}
                    doc={d}
                    active={artifactId === d.id}
                    onNavigate={() => onNavigateSource(d.id)}
                    onDelete={() => onDeleteSource(d)}
                  />
                ))}
              </Stack>
            )}
            {studyActive && <ExpandedModeNav mode={studyMode} onChange={setStudyMode} />}
          </ScrollArea>
        </SidebarAnimatedLayer>
      </Box>

      {/* Footer controls */}
      <Box
        style={{
          flexShrink: 0,
          overflow: "hidden",
          borderTop: wide ? "1px solid var(--mantine-color-default-border)" : undefined,
        }}
      >
        {wide ? (
          <Box p={isMobile ? "sm" : "md"} w="100%" pb={isMobile ? "calc(var(--mantine-spacing-sm) + env(safe-area-inset-bottom))" : undefined}>
            {username && (
              <Stack gap="xs" mb={isMobile ? "sm" : "md"}>
                <Group justify="space-between">
                  <Text size="xs" c="gray.5">
                    Storage
                  </Text>
                  <Text size="xs" c="gray.6">
                    {storagePct}%
                  </Text>
                </Group>
                <Progress value={storagePct} size="sm" color="lavender" />
              </Stack>
            )}
            {isAdmin && (
              <NavLink
                label="LLM models"
                description="Enable or disable chat models"
                leftSection={<IconCpu size={18} stroke={1.5} />}
                active={pathname === "/workspace/models"}
                onClick={onOpenModels}
                mb="sm"
                styles={{ root: { borderRadius: "var(--mantine-radius-md)" } }}
              />
            )}
            <Button
              fullWidth
              size={isMobile ? "md" : "sm"}
              radius="xl"
              leftSection={<IconUpload size={16} stroke={2} />}
              onClick={onAddSource}
              mb={isMobile ? "sm" : "md"}
              className="zivo-add-source"
            >
              Add source
            </Button>
            <Stack gap={10} mt={4}>
              <Group justify="space-between" align="center">
                <Text size="xs" c="dimmed" fw={500} style={{ fontFamily: "var(--font-sans)" }}>
                  Appearance
                </Text>
                <SegmentedControl
                  size="xs"
                  value={isDark ? "dark" : "light"}
                  onChange={(value) => {
                    if ((value === "dark") !== isDark) toggleColorScheme();
                  }}
                  data={[
                    {
                      value: "light",
                      label: (
                        <Center style={{ display: "flex", lineHeight: 1 }}>
                          <IconSun size={14} stroke={2} />
                        </Center>
                      ),
                    },
                    {
                      value: "dark",
                      label: (
                        <Center style={{ display: "flex", lineHeight: 1 }}>
                          <IconMoon size={14} stroke={2} />
                        </Center>
                      ),
                    },
                  ]}
                  aria-label={isDark ? "Dark mode on" : "Light mode on"}
                />
              </Group>
              <Group gap={4} align="center" grow>
                <Button
                  variant="subtle"
                  color="gray"
                  size="xs"
                  leftSection={<IconSettings size={15} stroke={1.7} />}
                  onClick={onOpenSettings}
                  px="xs"
                  styles={{ root: { height: 34, fontWeight: 500 }, label: { fontWeight: 500 } }}
                >
                  Settings
                </Button>
                <Button
                  variant="subtle"
                  color="gray"
                  size="xs"
                  leftSection={<IconLogin size={15} stroke={1.7} />}
                  onClick={onSignIn}
                  px="xs"
                  styles={{ root: { height: 34, fontWeight: 500 }, label: { fontWeight: 500 } }}
                >
                  {username ? `@${username}` : "Sign in"}
                </Button>
              </Group>
            </Stack>
          </Box>
        ) : (
          <Box pos="relative" mih={180} w="100%">
            <Stack gap={6} align="center" w="100%" py="xs">
              <MiniRailButton
                label={isDark ? "Switch to light mode" : "Switch to dark mode"}
                onClick={() => toggleColorScheme()}
              >
                {isDark ? <IconMoon size={18} stroke={1.5} /> : <IconSun size={18} stroke={1.5} />}
              </MiniRailButton>
              <MiniRailButton label="Settings" onClick={onOpenSettings}>
                <IconSettings size={18} stroke={1.5} />
              </MiniRailButton>
              <MiniRailButton label="Add source" emphasized onClick={onAddSource}>
                <IconUpload size={18} stroke={1.5} />
              </MiniRailButton>
              <MiniRailButton label={username ? `@${username}` : "Sign in"} onClick={onSignIn}>
                <IconLogin size={18} stroke={1.5} />
              </MiniRailButton>
            </Stack>
          </Box>
        )}
      </Box>
    </>
  );
}
